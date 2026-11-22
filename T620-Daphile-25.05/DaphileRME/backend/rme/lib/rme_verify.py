"""Bounded readback and absolute-write retries; never overwrite a third value."""
import time

def verify(before,expected,owned,read,indexes,equivalent,check,resend,max_reads=4,accepted_intermediate=None):
 original=indexes(before);retried=[];last=None
 for attempt in range(max_reads):
  if attempt:time.sleep(.15)
  current=read();actual=indexes(current)
  if current['usb_serial']!=before['usb_serial'] or set(actual)!=set(original):raise RuntimeError('RME identity/inventory changed; retry refused')
  missing=[]
  for location,value in actual.items():
   if location not in owned:
    if value!=original[location]:raise RuntimeError('Unrelated RME parameter changed; retry refused')
   elif not equivalent(location,value,expected[location]):
    if not equivalent(location,value,original[location]) and value not in (accepted_intermediate or {}).get(location,[]):raise RuntimeError('Concurrent RME control edit; retry refused')
    missing.append(location)
  if not missing:
   check(current)
   return current,{'readback_attempts':attempt+1,'retry_writes':retried,'verified':True}
  last=missing
  # First allow a delayed notification to settle without another SET. Then
  # resend only absolute requested values still equal to their originals.
  if attempt>=1 and attempt<max_reads-1:
   for location in sorted(missing):
    resend(location,expected[location]);retried.append({'address':location[0],'index':location[1]})
 raise RuntimeError('RME did not apply requested controls after bounded verification: '+str(last))
