"""Create Camilla profiles from AutoEq's oratory1990 measurement results."""
from pathlib import Path
import json, math, sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'DaphileCamillaDSP/backend'))
from peq.model import new_profile, new_band, validate, coefficients, response, RATES

# AutoEq ParametricEQ 10-band results; shelving frequency and Q in Hz and dB.
SOURCES=[
 ('headphones-denon-ah-d9200','Denon AH-D9200',-7.2,[
  ('Lowshelf',105,1.4,.70),('Peaking',190,-3.8,.77),('Peaking',2176,6.6,1.99),
  ('Peaking',1036,-2.7,1.43),('Peaking',420,2.1,2.13),('Highshelf',10000,-3.4,.70),
  ('Peaking',7028,3.5,1.56),('Peaking',5420,-6.3,3.42),('Peaking',4317,1.9,5.76),
  ('Peaking',42,-.4,2.39)]),
 ('headphones-hifiman-edition-xs','HiFiMAN Edition XS',-5.8,[
  ('Lowshelf',105,5.8,.70),('Peaking',87,-2.8,.33),('Peaking',1896,4,1.92),
  ('Peaking',2896,-3.7,3.11),('Peaking',1420,1.3,3.89),('Highshelf',10000,-5.8,.70),
  ('Peaking',5585,1.7,6),('Peaking',70,-.4,2.12),('Peaking',111,.4,1.64),
  ('Peaking',927,-.9,6)])]

def profiles():
 result=[]
 for ident,name,pregain,rows in SOURCES:
  profile=new_profile(name);profile['id']=ident;profile['mode']='linked';profile['enabled']=True
  profile['levels']['pre_gain']=pregain
  for channel in ('L','R'):
   profile['peq'][channel]=[new_band(kind,freq,gain,q) for kind,freq,gain,q in rows]
  result.append(validate(profile))
 return result

def main():
 result=profiles()
 report={}
 for profile in result:
  values=[]
  for rate in RATES:
   sos=[coefficients(b,rate) for b in profile['peq']['L']]
   frequencies=[20*(min(20000,rate/2*.999)/20)**(i/5999) for i in range(6000)]
   peak=max(sum(response(c,f,rate) for c in sos) for f in frequencies)
   values.append({'rate':rate,'max_boost_db':round(peak,4),
                  'net_peak_db':round(peak+profile['levels']['pre_gain'],4)})
  assert all(item['net_peak_db'] < 0 for item in values)
  report[profile['name']]=values
 (ROOT/'HEADPHONE_PROFILES.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
 (ROOT/'HEADROOM_REPORT.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
 print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
