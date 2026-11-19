import os
import time

from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import (
    CONFIG_DISPATCHER,
    MAIN_DISPATCHER,
    set_ev_cls,
)
from os_ken.ofproto import ofproto_v1_3
from os_ken.lib import hub


class UDPRecoveryController(app_manager.OSKenApp):

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    PRIMARY = {
        1: {1: 2, 2: 1},
        2: {1: 2, 2: 1},
        4: {1: 2, 2: 1},
    }

    ALTERNATE = {
        1: {1: 3, 3: 1},
        3: {1: 2, 2: 1},
        4: {1: 3, 3: 1},
    }

    # (switch id, port) at both ends of both primary-path links:
    # s1-s2 and s2-s4
    PRIMARY_PORTS = {
        (1, 2),
        (2, 1),
        (2, 2),
        (4, 2),
    }

    def __init__(self, *args, **kwargs):
        super(UDPRecoveryController, self).__init__(
            *args, **kwargs
        )

        self.datapaths = {}
        self.installed = {}  # dpid -> set of in_ports with flows
        self.current_mode = "primary"
        self.primary_link_failed = False

        self.primary_port_up = {k: True for k in self.PRIMARY_PORTS}

        # STATIC_ROUTING=1 -> baseline: no failover, primary flows only
        self.static = os.environ.get("STATIC_ROUTING") == "1"

        self.mode_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "route_mode.txt",
        )

        # Always start from a known state; a stale file must not
        # push the network onto the alternate path at startup.
        self.write_mode_file("primary")

        if self.static:
            self.log("STATIC baseline mode: failover disabled")
        else:
            hub.spawn(self.monitor_route_mode)

    def log(self, msg):
        print(f"[{time.time():.3f}] {msg}", flush=True)

    def write_mode_file(self, mode):
        try:
            with open(self.mode_file, "w") as f:
                f.write(mode + "\n")
        except OSError as e:
            self.logger.error(
                "Could not update route mode file: %s", e
            )

    def install_routes(self, dp, routes, wipe=False):
        """Install in_port -> out_port flows.

        wipe=True (fresh switch connection): delete everything first.
        wipe=False (mode change): make-before-break. New flows are added
        first (same match+priority replaces the old flow in place), then
        only flows for in_ports that are no longer used are removed, so
        there is no blackout window during the switchover.
        """
        parser = dp.ofproto_parser
        ofp = dp.ofproto

        if wipe:
            dp.send_msg(
                parser.OFPFlowMod(
                    datapath=dp,
                    command=ofp.OFPFC_DELETE,
                    out_port=ofp.OFPP_ANY,
                    out_group=ofp.OFPG_ANY,
                    match=parser.OFPMatch(),
                )
            )

        for in_port, out_port in routes.items():
            match = parser.OFPMatch(in_port=in_port)
            actions = [parser.OFPActionOutput(out_port)]
            instructions = [
                parser.OFPInstructionActions(
                    ofp.OFPIT_APPLY_ACTIONS, actions
                )
            ]
            dp.send_msg(
                parser.OFPFlowMod(
                    datapath=dp,
                    priority=100,
                    match=match,
                    instructions=instructions,
                )
            )

        if not wipe:
            # remove stale flows: in_ports installed earlier but unused now
            for in_port in self.installed.get(dp.id, set()) - set(routes):
                dp.send_msg(
                    parser.OFPFlowMod(
                        datapath=dp,
                        command=ofp.OFPFC_DELETE,
                        out_port=ofp.OFPP_ANY,
                        out_group=ofp.OFPG_ANY,
                        match=parser.OFPMatch(in_port=in_port),
                    )
                )

        self.installed[dp.id] = set(routes)

    def apply_mode(self, mode):
        if mode not in ("primary", "alternate"):
            return

        routes = (
            self.PRIMARY
            if mode == "primary"
            else self.ALTERNATE
        )

        for dp in list(self.datapaths.values()):
            self.install_routes(
                dp, routes.get(dp.id, {})
            )

        self.current_mode = mode
        self.log(f"SDN route changed to: {mode.upper()} (flows sent)")

    def monitor_route_mode(self):
        while True:
            try:
                with open(self.mode_file, "r") as f:
                    mode = f.read().strip().lower()

                if mode not in ("primary", "alternate"):
                    hub.sleep(1)
                    continue

                if self.primary_link_failed:
                    if mode != "alternate":
                        self.write_mode_file("alternate")
                    if self.current_mode != "alternate":
                        self.apply_mode("alternate")

                elif mode != self.current_mode:
                    self.apply_mode(mode)

            except FileNotFoundError:
                pass
            except Exception as e:
                self.logger.error(
                    "Route monitor error: %s", e
                )

            hub.sleep(1)

    @set_ev_cls(
        ofp_event.EventOFPSwitchFeatures,
        CONFIG_DISPATCHER,
    )
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        self.datapaths[dp.id] = dp

        mode = (
            "alternate"
            if self.primary_link_failed
            else self.current_mode
        )

        routes = (
            self.PRIMARY
            if mode == "primary"
            else self.ALTERNATE
        )

        self.install_routes(
            dp, routes.get(dp.id, {}), wipe=True
        )

        self.log(f"Switch {dp.id} connected; mode={mode}")

    @set_ev_cls(
        ofp_event.EventOFPPortStatus,
        MAIN_DISPATCHER,
    )
    def port_status_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath
        port_no = msg.desc.port_no
        ofp = dp.ofproto

        key = (dp.id, port_no)

        if key not in self.PRIMARY_PORTS:
            return

        link_down = (
            msg.reason == ofp.OFPPR_DELETE
            or bool(msg.desc.state & ofp.OFPPS_LINK_DOWN)
        )

        self.log(
            f"PortStatus s{dp.id} port {port_no}: "
            f"{'DOWN' if link_down else 'UP'}"
        )

        if self.static:
            return

        if link_down:
            self.primary_port_up[key] = False

            if not self.primary_link_failed:
                self.primary_link_failed = True

                self.log("Primary link failure detected!")

                self.apply_mode("alternate")
                self.write_mode_file("alternate")

        else:
            self.primary_port_up[key] = True

            both_ends_up = all(
                self.primary_port_up[p]
                for p in self.PRIMARY_PORTS
            )

            if (
                self.primary_link_failed
                and both_ends_up
            ):
                self.primary_link_failed = False

                self.log("Primary link restored!")

                self.apply_mode("primary")
                self.write_mode_file("primary")
