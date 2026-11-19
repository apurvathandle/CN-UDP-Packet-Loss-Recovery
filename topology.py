from mininet.topo import Topo


class UDPRecoveryTopo(Topo):
    def build(self):
        h1 = self.addHost('h1', ip='10.0.0.1/24')
        h2 = self.addHost('h2', ip='10.0.0.2/24')

        s1 = self.addSwitch('s1', protocols='OpenFlow13')
        s2 = self.addSwitch('s2', protocols='OpenFlow13')
        s3 = self.addSwitch('s3', protocols='OpenFlow13')
        s4 = self.addSwitch('s4', protocols='OpenFlow13')

        self.addLink(h1, s1)
        self.addLink(s4, h2)

        self.addLink(s1, s2)
        self.addLink(s2, s4)

        self.addLink(s1, s3)
        self.addLink(s3, s4)


topos = {'udprecovery': lambda: UDPRecoveryTopo()}
