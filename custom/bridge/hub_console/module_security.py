"""Network/origin boundary for optional standalone module gateways only."""
import ipaddress
from fastapi.responses import Response

def same_origin(headers, settings):
    origin=headers.get('origin')
    if not origin:return True
    return origin in {'https://'+headers.get('host',''), 'http://'+headers.get('host','')} or (settings.testing and origin=='http://testserver')

class NetworkGuard:
    def __init__(self, app, networks, settings):
        self.app=app;self.networks=[ipaddress.ip_network(v) for v in networks];self.settings=settings
    async def __call__(self, scope, receive, send):
        if scope['type'] not in {'http','websocket'}:return await self.app(scope,receive,send)
        ip=(scope.get('client') or ('',0))[0]
        try:allowed=any(ipaddress.ip_address(ip) in net for net in self.networks)
        except ValueError:allowed=self.settings.testing and ip in {'testclient','testserver'}
        if not allowed:
            if scope['type']=='websocket':return await send({'type':'websocket.close','code':1008})
            return await Response('Network not allowed',status_code=403)(scope,receive,send)
        return await self.app(scope,receive,send)
