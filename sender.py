"""UDP sender: single event loop doing paced sending, ACK/NACK handling and
timeout retransmission, plus metrics (first-attempt loss, recovery time)."""
import argparse
import json
import select
import socket
import time

ap = argparse.ArgumentParser()
ap.add_argument("--src", default="10.0.0.1")
ap.add_argument("--dst", default="10.0.0.2")
ap.add_argument("--port", type=int, default=5005)
ap.add_argument("--count", type=int, default=300)
ap.add_argument("--rate", type=float, default=50, help="packets per second")
ap.add_argument("--timeout", type=float, default=0.5, help="ACK timeout (s)")
ap.add_argument("--max-retries", type=int, default=8)
ap.add_argument("--session", type=int, default=int(time.time() * 1000) % 10**9)
ap.add_argument("--log", default=None, help="write JSON results here")
ap.add_argument("--verbose", action="store_true")
args = ap.parse_args()

RECEIVER = (args.dst, args.port)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((args.src, 0))
sock.setblocking(False)

first_sent = {}      # seq -> wall time of first transmission
last_sent = {}       # seq -> monotonic time of latest transmission
pending = set()
retries = {}         # seq -> number of retransmissions
ack_times = []       # wall-clock time of each first ACK
acked = set()
failed = set()
total_sent = 0
timeout_retx = 0
nack_retx = 0


def tx(obj):
    sock.sendto(json.dumps(obj).encode(), RECEIVER)


def send_data(seq, reason=None):
    global total_sent, timeout_retx, nack_retx
    if seq in first_sent:
        retries[seq] = retries.get(seq, 0) + 1
        if reason == "nack":
            nack_retx += 1
        else:
            timeout_retx += 1
    else:
        first_sent[seq] = time.time()
    tx({"type": "DATA", "session": args.session, "seq": seq})
    last_sent[seq] = time.monotonic()
    pending.add(seq)
    total_sent += 1
    if args.verbose:
        print(f"Sent {seq}" + (f" (retx:{reason})" if reason else ""))


def handle(msg):
    if msg.get("session") != args.session:
        return
    kind = msg.get("type")
    if kind == "ACK":
        seq = msg["seq"]
        if seq in pending:
            pending.discard(seq)
            acked.add(seq)
            ack_times.append(time.time())
    elif kind == "NACK":
        now = time.monotonic()
        for seq in msg.get("missing", []):
            # always allow a NACK-driven retry of a first transmission;
            # for packets already retransmitted, wait 100 ms to avoid duplicates
            fresh = retries.get(seq, 0) == 0
            if (seq in pending
                    and (fresh or now - last_sent[seq] > 0.1)
                    and retries.get(seq, 0) < args.max_retries):
                send_data(seq, "nack")


def drain():
    while True:
        r, _, _ = select.select([sock], [], [], 0)
        if not r:
            return
        data, _ = sock.recvfrom(4096)
        try:
            handle(json.loads(data.decode()))
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError):
            pass


interval = 1.0 / args.rate
start_mono = time.monotonic()
start_wall = time.time()
next_seq = 1
print(f"[{start_wall:.3f}] Sending {args.count} packets at {args.rate}/s "
      f"(session {args.session})", flush=True)

while next_seq <= args.count or pending:
    now = time.monotonic()

    # 1) paced sending (catch up if we fell behind)
    while next_seq <= args.count and now >= start_mono + (next_seq - 1) * interval:
        send_data(next_seq)
        next_seq += 1

    # 2) wait for ACK/NACK, never longer than the next send slot
    wait = 0.02
    if next_seq <= args.count:
        wait = max(0.0, min(wait, start_mono + (next_seq - 1) * interval - now))
    r, _, _ = select.select([sock], [], [], wait)
    if r:
        drain()

    # 3) timeout retransmission
    now = time.monotonic()
    for seq in list(pending):
        if now - last_sent[seq] >= args.timeout:
            if retries.get(seq, 0) < args.max_retries:
                send_data(seq, "timeout")
            else:
                pending.discard(seq)
                failed.add(seq)
                print(f"Packet {seq} failed after {args.max_retries} retries")

# END with retry until REPORT arrives
report = None
for _ in range(5):
    tx({"type": "END", "session": args.session, "total": args.count})
    deadline = time.monotonic() + 1.0
    while report is None and time.monotonic() < deadline:
        r, _, _ = select.select([sock], [], [], 0.1)
        if r:
            data, _ = sock.recvfrom(4096)
            try:
                m = json.loads(data.decode())
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if m.get("type") == "REPORT" and m.get("session") == args.session:
                report = m
    if report:
        break

end_wall = time.time()
elapsed = end_wall - start_wall

# Recovery time = longest silence between consecutive ACKs
max_gap, gap_start, gap_end = 0.0, None, None
ack_sorted = sorted(ack_times)
for a, b in zip(ack_sorted, ack_sorted[1:]):
    if b - a > max_gap:
        max_gap, gap_start, gap_end = b - a, a, b

retried = sum(1 for s in first_sent if retries.get(s, 0) > 0)
received = report["received"] if report else None
missing = report["missing"] if report else None
result = {
    "session": args.session, "count": args.count, "rate": args.rate,
    "total_sent": total_sent,
    "retransmissions": timeout_retx + nack_retx,
    "timeout_retx": timeout_retx, "nack_retx": nack_retx,
    "first_attempt_loss_pct": round(100.0 * retried / args.count, 2),
    "acked": len(acked), "gave_up": sorted(failed),
    "received": received, "missing": missing,
    "final_loss_pct": (round(100.0 * (args.count - received) / args.count, 2)
                       if report else None),
    "elapsed_s": round(elapsed, 3),
    "max_ack_gap_s": round(max_gap, 3),
    "gap_start": gap_start, "gap_end": gap_end,
    "start_wall": start_wall, "end_wall": end_wall,
    "report_received": report is not None,
}

print("\n--- Sender report ---")
print(f"Packets intended:            {args.count}")
print(f"Packets sent (with retries): {total_sent}")
print(f"Retransmissions:             {result['retransmissions']} "
      f"(timeout {timeout_retx}, NACK {nack_retx})")
print(f"First-attempt loss:          {result['first_attempt_loss_pct']}% "
      f"({retried} packets needed a retry)")
print(f"Final loss:                  {result['final_loss_pct']}%")
print(f"Missing at receiver:         {missing}")
print(f"Longest ACK gap (recovery):  {result['max_ack_gap_s']} s")
print(f"Elapsed:                     {result['elapsed_s']} s")
if not report:
    print("WARNING: receiver report timed out")

if args.log:
    with open(args.log, "w") as f:
        json.dump(result, f, indent=2)
sock.close()
