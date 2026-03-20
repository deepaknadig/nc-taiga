import vobject
from datetime import datetime

v = vobject.iCalendar()
v.add('vtodo')

try:
    # Try naive UTC string format directly which vobject parses natively
    # VObject handles string assignments well for properties like COMPLETED
    dt_str = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    v.vtodo.add('completed').value = dt_str
    print("Success with string format:", v.serialize())
except Exception as e:
    print(f"String format failed: {e}")

try:
    import pytz
    v = vobject.iCalendar()
    v.add('vtodo')
    # Maybe assigning tzinfo directly to the component works better
    dt = datetime.utcnow().replace(tzinfo=pytz.utc)
    v.vtodo.add('completed').value = dt
    print("Success with pytz:", v.serialize())
except Exception as e:
    print(f"pytz.utc failed: {e}")
