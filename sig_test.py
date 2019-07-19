from ophyd import Device, Component as Cpt

from ads_pcds import AdsSignal

# sig = AdsSignal('ads://172.21.148.145/Main.iCycle', name='sig')
sig = AdsSignal('ads://172.21.148.145/@1/Main.iCycle', name='sig')


class MyDevice(Device):
    icycle_polled = Cpt(AdsSignal, 'Main.iCycle', poll_rate=1.0)
    brake = Cpt(AdsSignal, 'Main.M1.bBrake')


dev = MyDevice('ads://172.21.148.145/', name='dev')
