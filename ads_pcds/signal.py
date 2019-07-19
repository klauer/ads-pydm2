import logging
import time

from ophyd import Signal

from .ads import get_connection, Symbol
from .util import parse_address, make_address


logger = logging.getLogger(__name__)


class _SignalSymbol(Symbol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.callbacks = []

    def value_updated(self, timestamp, value):
        for cb in list(self.callbacks):
            try:
                cb(timestamp, value)
            except Exception:
                logger.exception('Symbol update failed: %s(%s, %r)',
                                 cb.__name__, timestamp, value)
                self.callbacks.remove(cb)

    def stop(self):
        if self.callbacks:
            return
        return super().stop()


class AdsSignal(Signal):
    def __init__(self, read_pv, *, ip_address=None, ams_id=None, port=None,
                 poll_rate=None, name=None, **kwargs):
        if name is None:
            name = read_pv

        super().__init__(name=name, **kwargs)

        if '/' not in read_pv:
            symbol = read_pv
        else:
            info = parse_address(read_pv)
            ip_address = info['ip_address'] or ip_address
            ams_id = info['ams_id'] or ams_id
            port = info['port'] or port
            symbol = info['symbol'] or symbol
            poll_rate = info['poll_rate'] or poll_rate

        self.ip_address = ip_address
        self.ams_id = ams_id or (ip_address and ip_address + '.1.1')
        self.port = port or 851
        self.poll_rate = poll_rate
        self.symbol = symbol
        self.ads_address = make_address(
            self.ip_address, self.ams_id, self.port, self.symbol,
            poll_rate=self.poll_rate)

        if self.ip_address is None:
            raise ValueError(f'IP address unset: {self.ads_address}')

        self.plc = get_connection(self.ip_address, self.ams_id, self.port)
        self._symbol = self.plc.get_symbol(self.symbol, self.poll_rate,
                                           cls=_SignalSymbol)
        self._symbol.update_hook = self._value_changed
        self._subscribed = False

    def _repr_info(self):
        yield from super()._repr_info()
        yield ('ads_address', self.ads_address)

    def get(self):
        'Read the Symbol value over ADS'
        value = self._symbol.read()
        super().put(value=value, timestamp=time.time(), force=True)
        if not self._subscribed:
            self._run_metadata_callbacks()
        return value

    def put(self, value):
        'Write to the Symbol over ADS'
        self._symbol.write(value)
        super().put(value=value, timestamp=time.time(), force=True)

    def _value_changed(self, timestamp, value):
        'ADS callback indicating that the value has changed'
        # super().put updates value+metadata and runs SUB_VALUE
        super().put(value=value, timestamp=timestamp, force=True)
        self._run_metadata_callbacks()

    def subscribe(self, callback, event_type=None, run=True):
        if event_type is None:
            event_type = self._default_sub

        if not self._subscribed and event_type in {'value', 'meta'}:
            self._symbol.callbacks.append(self._value_changed)
            self._symbol.start()
            self._subscribed = True

        return super().subscribe(callback, event_type=event_type, run=run)

    def _stop_subscription(self):
        self._symbol.callbacks.remove(self._value_changed)
        self._symbol.stop()
        self._subscribed = False

    def unsubscribe_all(self):
        super().unsubscribe_all()
        self._stop_subscription()

    def unsubscribe(self, cid):
        super().unsubscribe(cid)
        if not any(cb for cb in self._callbacks.values()):
            self._stop_subscription()

    def destroy(self):
        if self._symbol is None:
            return

        try:
            self._symbol.callbacks.remove(self._value_changed)
        except ValueError:
            ...
        self._symbol.stop()
        self._symbol = None
        return super().destroy()
