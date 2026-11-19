#!/usr/bin/env python3
"""Automated experiments: static baseline vs SDN recovery.

Run with sudo from the project folder:
    sudo env "PATH=$PATH" python3 run_experiment.py

For every (mode, scenario, run) it starts a fresh controller, builds the
Mininet topology, runs receiver + sender, injects the link failure at the
right moment, and appends one row to results/results.csv.
"""
import argparse
import csv
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mininet.net import Mininet            # noqa: E402
from mininet.node import RemoteController, OVSSwitch  # noqa: E402
from mininet.log import setLogLevel        # noqa: E402
from topology import UDPRecoveryTopo       # noqa: E402

RESULTS = os.path.join(HERE, "results")
RUNS_DIR = os.path.join(RESULTS, "runs")
CSV_PATH = os.path.join(RESULTS, "results.csv")
CTRL_PORT = 6653
FAIL_AT = 3.0   # seconds after the sender starts

SCENARIOS = {
    "none":  {"count": 500, "outage": 0},    # no failure
    "short": {"count": 500, "outage": 4},    # s1-s2 down for 4 s
    "long":  {"count": 800, "outage": 10},   # s1-s2 down for 10 s
}

# Loss-type tests: the link stays UP, packets are dropped with tc netem.
#   random  : 10% independent loss on the data direction (s1 -> s2)
#   burst   : bursty loss (Gilbert-Elliott model): ~9% overall, bursts of
#             ~3 packets (go-bad 3%, back-to-good 30%, drop all when bad)
#   ackloss : 10% loss on the return direction (ACKs are lost, data is not)
# modes: "recovery" = our protocol (NACK + retransmission)
#        "plain"    = plain UDP, no retransmission (--max-retries 0)
LOSS_TESTS = {
    "random":  {"count": 500, "outage": 0,
                "netem": [("s1", "s1-eth2", "loss 10%")]},
    "burst":   {"count": 500, "outage": 0,
                "netem": [("s1", "s1-eth2", "loss gemodel 3% 30% 100% 0%")]},
    "ackloss": {"count": 500, "outage": 0,
                "netem": [("s2", "s2-eth1", "loss 10%")]},
}
SCENARIOS.update(LOSS_TESTS)

FIELDS = [
    "mode", "scenario", "run", "count", "rate", "outage_s",
    "total_sent", "retransmissions", "nack_retx",
    "first_attempt_loss_pct", "final_loss_pct", "missing_count",
    "max_ack_gap_s", "elapsed_s", "ctrl_detect_ms", "ctrl_reroute_ms",
]


def start_controller(osken, mode, tag):
    env = dict(os.environ)
    if mode == "static":
        env["STATIC_ROUTING"] = "1"
    else:
        env.pop("STATIC_ROUTING", None)
    logpath = os.path.join(RUNS_DIR, f"{tag}_controller.log")
    logf = open(logpath, "w")
    proc = subprocess.Popen(
        [osken, os.path.join(HERE, "controller.py")],
        cwd=HERE, env=env, stdout=logf, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"controller exited early; see {logpath}")
        try:
            socket.create_connection(("127.0.0.1", CTRL_PORT), 0.5).close()
            return proc, logpath, logf
        except OSError:
            time.sleep(0.3)
    raise RuntimeError("controller did not start listening on 6653")


def stop_controller(proc, logf):
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
    logf.close()
    time.sleep(1)


def wait_switches(logpath, want=4, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with open(logpath) as f:
            if f.read().count("connected; mode") >= want:
                return True
        time.sleep(0.3)
    return False


def ctrl_times(logpath):
    """Timestamps of failure detection and reroute from the controller log."""
    text = open(logpath).read()
    d = re.search(r"\[(\d+\.\d+)\] Primary link failure detected", text)
    r = re.search(r"\[(\d+\.\d+)\] SDN route changed to: ALTERNATE", text)
    return (float(d.group(1)) if d else None,
            float(r.group(1)) if r else None)


def run_one(args, mode, scen, n):
    cfg = SCENARIOS[scen]
    tag = f"{mode}_{scen}_{n}"
    json_path = os.path.join(RUNS_DIR, f"{tag}.json")
    # "plain"/"recovery" only differ in the sender; controller runs normally
    ctrl_mode = "static" if mode == "static" else "sdn"
    ctrl, logpath, logf = start_controller(args.osken, ctrl_mode, tag)
    net = rp = sp = None
    try:
        net = Mininet(topo=UDPRecoveryTopo(), switch=OVSSwitch,
                      controller=None)
        net.addController("c0", controller=RemoteController,
                          ip="127.0.0.1", port=CTRL_PORT)
        net.start()
        h1, h2 = net.get("h1", "h2")
        # switches connect to the controller only after net.start()
        if not wait_switches(logpath):
            print("  !! switches did not connect; skipping run")
            return None
        time.sleep(1)   # let the flows settle

        ok = False
        for _ in range(4):
            if net.ping([h1, h2]) == 0:
                ok = True
                break
            time.sleep(1)
        if not ok:
            print("  !! hosts cannot ping; skipping run")
            return None

        for sw, intf, rule in cfg.get("netem", []):
            out = net.get(sw).cmd(
                f"tc qdisc add dev {intf} root netem {rule}")
            if out.strip():
                print(f"  !! netem on {intf} failed: {out.strip()}")
                return None

        rp = h2.popen(
            ["python3", os.path.join(HERE, "receiver.py")],
            stdout=open(os.path.join(RUNS_DIR, f"{tag}_receiver.log"), "w"),
            stderr=subprocess.STDOUT)
        time.sleep(0.5)

        extra = ["--max-retries", "0"] if mode == "plain" else []
        sp = h1.popen(
            ["python3", os.path.join(HERE, "sender.py"),
             "--count", str(cfg["count"]), "--rate", str(args.rate),
             "--log", json_path] + extra,
            stdout=open(os.path.join(RUNS_DIR, f"{tag}_sender.log"), "w"),
            stderr=subprocess.STDOUT)

        t_down = None
        if cfg["outage"] > 0:
            time.sleep(FAIL_AT)
            t_down = time.time()
            net.configLinkStatus("s1", "s2", "down")
            time.sleep(cfg["outage"])
            net.configLinkStatus("s1", "s2", "up")

        limit = cfg["count"] / args.rate + cfg["outage"] + 40
        try:
            sp.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            sp.kill()
            print("  !! sender timed out")

        if not os.path.exists(json_path):
            print("  !! no result file; see", tag + "_sender.log")
            return None
        r = json.load(open(json_path))

        det, rer = ctrl_times(logpath)
        detect_ms = (round((det - t_down) * 1000, 1)
                     if det and t_down else "")
        reroute_ms = (round((rer - det) * 1000, 1)
                      if det and rer else "")
        missing = r["missing"]
        return {
            "mode": mode, "scenario": scen, "run": n,
            "count": r["count"], "rate": r["rate"],
            "outage_s": cfg["outage"],
            "total_sent": r["total_sent"],
            "retransmissions": r["retransmissions"],
            "nack_retx": r["nack_retx"],
            "first_attempt_loss_pct": r["first_attempt_loss_pct"],
            "final_loss_pct": (r["final_loss_pct"]
                               if r["final_loss_pct"] is not None else 100.0),
            "missing_count": (len(missing) if missing is not None
                              else r["count"]),
            "max_ack_gap_s": r["max_ack_gap_s"],
            "elapsed_s": r["elapsed_s"],
            "ctrl_detect_ms": detect_ms,
            "ctrl_reroute_ms": reroute_ms,
        }
    finally:
        for p in (sp, rp):
            if p is not None and p.poll() is None:
                p.terminate()
        if net is not None:
            try:
                net.stop()
            except Exception:
                pass
        stop_controller(ctrl, logf)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--rate", type=float, default=50)
    ap.add_argument("--modes", default="static,sdn")
    ap.add_argument("--scenarios", default="none,short,long")
    ap.add_argument("--loss-tests", action="store_true",
                    help="run the netem loss tests (plain vs recovery); "
                         "adds to results.csv")
    ap.add_argument("--loss-scenarios", default="random,burst,ackloss")
    ap.add_argument("--osken", default="osken-manager",
                    help="path to the osken-manager program")
    ap.add_argument("--append", action="store_true",
                    help="add to results.csv instead of starting a new one")
    args = ap.parse_args()

    if os.geteuid() != 0:
        sys.exit("Run with sudo:  sudo env \"PATH=$PATH\" python3 run_experiment.py")

    os.makedirs(RUNS_DIR, exist_ok=True)
    if os.path.exists(CSV_PATH) and not (args.append or args.loss_tests):
        os.rename(CSV_PATH, CSV_PATH.replace(
            ".csv", f"_old_{int(time.time())}.csv"))
    new_file = not os.path.exists(CSV_PATH)

    setLogLevel("warning")
    subprocess.run(["mn", "-c"], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)

    if args.loss_tests:
        pairs = [(m, sc) for sc in args.loss_scenarios.split(",")
                 for m in ("plain", "recovery")]
    else:
        pairs = [(m, sc) for m in args.modes.split(",")
                 for sc in args.scenarios.split(",")]
    total = len(pairs) * args.runs
    done = 0
    for mode, scen in pairs:
        for n in range(1, args.runs + 1):
            done += 1
            print(f"[{done}/{total}] mode={mode} scenario={scen} "
                  f"run={n} ...", flush=True)
            try:
                row = run_one(args, mode, scen, n)
            except Exception as e:
                print("  !! run failed:", e)
                subprocess.run(["mn", "-c"],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
                continue
            if row is None:
                continue
            with open(CSV_PATH, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                if new_file:
                    w.writeheader()
                    new_file = False
                w.writerow(row)
            print(f"  gap={row['max_ack_gap_s']}s  "
                  f"missing={row['missing_count']}  "
                  f"retx={row['retransmissions']}  "
                  f"first-try-loss={row['first_attempt_loss_pct']}%",
                  flush=True)

    print("\nDone. Now run:  python3 summarize.py")


if __name__ == "__main__":
    main()
