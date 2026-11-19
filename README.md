# UDP Packet Loss Detection and Recovery with SDN Rerouting

## 1. Project Overview

This project implements a UDP packet loss detection and recovery system using Software Defined Networking (SDN).

UDP does not guarantee packet delivery. This project addresses packet loss through application-level acknowledgements, retransmissions, and SDN-based rerouting when network link failures occur.

## 2. Objectives

- Detect packet loss during UDP communication.
- Implement ACK and NACK mechanisms.
- Recover missing packets through retransmissions.
- Use SDN to reroute traffic when primary network links fail.
- Compare static routing and SDN-based recovery.
- Evaluate performance under different packet loss conditions.

## 3. Network Architecture

The network consists of:
- Two hosts: h1 and h2.
- Four Open vSwitch switches: s1, s2, s3 and s4.
- An OS-Ken SDN controller.
- Mininet for network emulation.

### Network Paths

**Primary path:**

h1 → s1 → s2 → s4 → h2

**Alternate path:**

h1 → s1 → s3 → s4 → h2

The SDN controller monitors primary-path link failures and installs alternate flow rules when necessary.

## 4. Technologies Used

- Python
- Mininet
- Open vSwitch (OVS)
- OS-Ken SDN Controller
- OpenFlow 1.3
- UDP Sockets
- Linux Traffic Control (tc netem)
- Matplotlib

## 5. Implementation

| File | Description |
|---|---|
| `topology.py` | Creates the Mininet network topology. |
| `controller.py` | Implements SDN routing and link failure monitoring. |
| `sender.py` | Sends numbered UDP packets and handles ACK/NACK and retransmissions. |
| `receiver.py` | Receives packets, detects missing sequence numbers and sends acknowledgements. |
| `run.py` | Provides network execution functionality. |
| `run_experiment.py` | Automates network experiments and failure scenarios. |
| `summarize.py` | Processes experiment results and generates graphs and summaries. |

## 6. Packet Communication

The implementation uses the following message types:

- **DATA:** Carries data packets from sender to receiver.
- **ACK:** Confirms successful packet reception.
- **NACK:** Identifies missing packets.
- **END:** Indicates completion of transmission.
- **REPORT:** Provides the final packet reception report.

Sequence numbers help identify missing and duplicate packets.

## 7. Experiments

The system is evaluated under the following conditions:

1. Normal network operation without failures.
2. Short-duration link failure.
3. Long-duration link failure.
4. Random packet loss.
5. Burst packet loss.
6. ACK packet loss.

The experiments compare static routing, SDN rerouting and application-level packet recovery.

## 8. Results

The experiments demonstrate:

- SDN rerouting significantly reduces recovery time during link failures.
- Application-level retransmissions recover missing data packets under random and burst loss conditions.
- ACK loss can cause additional retransmissions without necessarily causing final data loss.
- The system improves UDP reliability through packet detection and recovery mechanisms.

Detailed experimental data, CSV files, JSON results and graphs are available in the `results/` directory.

## 9. Limitations

- The implementation uses a single sender and receiver.
- The SDN controller represents a single point of failure.
- Security and congestion-control mechanisms are outside the current project scope.
- Experiments are conducted in a Mininet-emulated environment.

## 10. Repository Structure

```text
CN-UDP-Packet-Loss-Recovery/
├── topology.py
├── controller.py
├── sender.py
├── receiver.py
├── run.py
├── run_experiment.py
├── summarize.py
├── route_mode.txt
├── .gitignore
├── README.md
└── results/
    ├── results.csv
    ├── summary.md
    ├── graphs
    └── runs/
```

## 11. Conclusion

This project demonstrates how application-level reliability mechanisms and SDN-based rerouting can improve UDP communication during packet loss and network link failures.

By combining packet sequence tracking, acknowledgements, retransmissions and dynamic routing, the implementation provides a more reliable UDP communication system.
