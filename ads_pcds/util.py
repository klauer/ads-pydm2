def parse_address(addr):
    'ads://<host>[:<port>][/@poll_rate]/<symbol>'
    if addr.startswith('ads:'):
        addr = addr[4:].lstrip('/')

    host_info, _, symbol = addr.partition('/')

    if ':' in host_info:
        host, port = host_info.split(':')
    else:
        host, port = host_info, 851

    if '@' in host:
        ams_id, ip_address = host.split('@')
    elif host.count('.') == 3:
        ip_address = host
        ams_id = '{}.1.1'.format(ip_address)
    elif host.count('.') == 5:
        ams_id = host
        if not ams_id.endswith('.1.1'):
            raise ValueError('Cannot assume IP address without an AMS ID '
                             'that ends with .1.1')
        ip_address = ams_id[:-4]
    else:
        raise ValueError(f'Cannot parse host string: {host!r}')

    if symbol.startswith('@') and '/' in symbol:
        poll_info, _, symbol = symbol.partition('/')
        poll_rate = poll_info.lstrip('@')
    else:
        poll_rate = None

    return {'ip_address': ip_address,
            'host': host,
            'ams_id': ams_id,
            'port': int(port),
            'poll_rate': float(poll_rate) if poll_rate is not None else None,
            'symbol': symbol,
            }


def make_address(ip_address, ams_id, port, symbol, *, poll_rate=None):
    poll_info = '' if poll_rate is None else f'/@{poll_rate}'

    if ip_address and (ip_address + '.1.1' == ams_id):
        host = ip_address
    else:
        host = f'{ams_id}@{ip_address}'

    port_info = f'@{port}' if port != 851 else ''
    return f'ads://{host}{port_info}{poll_info}/{symbol}'
