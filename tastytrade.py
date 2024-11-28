from tastytrade import ProductionSession
from tastytrade import DXLinkStreamer
from tastytrade.dxfeed import EventType
from tastytrade.instruments import get_option_chain
from datetime import date

session = ProductionSession('username', 'password')

chain = get_option_chain(session, 'SPLG')
subs_list = [chain[date(2023, 6, 16)][0].streamer_symbol]

await streamer.subscribe(EventType.GREEKS, subs_list)
greeks = await streamer.get_event(EventType.GREEKS)
print(greeks)
