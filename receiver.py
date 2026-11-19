"""UDP receiver with sequence-gap detection (NACK), ACKs and per-session state."""
import argparse
import json
import socket
import time

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="10.0.0.2")
ap.add_argument("--port", type=int, default=5005)
args = ap.parse_args()

server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
server.bind((args.host, args.port))
print(f"UDP receiver listening on {args.host}:{args.port}", flush=True)

# (client_ip, session) -> state. A new session ID means fresh state,
# so repeated runs never contaminate each other.
sessions = {}


def send(obj, addr):
    server.sendto(json.dumps(obj).encode(), addr)


while True:
    data, addr = server.recvfrom(4096)
    try:
        pkt = json.loads(data.decode())
        kind = pkt.get("type")
        session = pkt["session"]
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, AttributeError):
        continue

    st = sessions.setdefault(
        (addr[0], session),
        {"received": set(), "highest": 0, "dups": 0, "nacks": 0},
    )

    if kind == "DATA":
        seq = pkt["seq"]
        if seq in st["received"]:
            st["dups"] += 1
        st["received"].add(seq)
        send({"type": "ACK", "session": session, "seq": seq}, addr)

        # Gap detection: a sequence number beyond the next expected one
        # means everything in between is missing.
        if seq > st["highest"] + 1:
            missing = [s for s in range(st["highest"] + 1, seq)
                       if s not in st["received"]]
            if missing:
                st["nacks"] += 1
                send({"type": "NACK", "session": session,
                      "missing": missing[:100]}, addr)
                print(f"[{time.time():.3f}] gap before {seq}: "
                      f"NACK for {len(missing)} packet(s)", flush=True)
        st["highest"] = max(st["highest"], seq)

    elif kind == "END":
        total = pkt["total"]
        missing = sorted(set(range(1, total + 1)) - st["received"])
        send({"type": "REPORT", "session": session,
              "received": len(st["received"]), "total": total,
              "missing": missing, "duplicates": st["dups"],
              "nacks_sent": st["nacks"]}, addr)
        print(f"\n--- Receiver report (client {addr[0]}, session {session}) ---")
        print(f"Expected: {total}  Unique received: {len(st['received'])}  "
              f"Duplicates: {st['dups']}  NACKs sent: {st['nacks']}")
        print(f"Missing: {missing}", flush=True)
