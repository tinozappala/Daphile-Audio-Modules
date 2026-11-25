# SPDX-License-Identifier: MIT
# Tino Zappala, Milano - 2026
# Userspace mathematical model, not execution of the kernel code.
import random,json
from fractions import Fraction
rng=random.Random(612117300)
mask=(1<<64)-1
N=10**9
for i in range(100000):
 rate=rng.randint(8000,768000);shift=rng.randint(80000,120000);den=N*shift;step=rate*100000
 elapsed=rng.randrange(1<<64) if i%7==0 else rng.randrange(10**11)
 frac=rng.randrange(den)
 q=elapsed*step//den
 rem=((elapsed*step-q*den)&mask)+frac
 if rem>=den:q+=1;rem-=den
 assert (q,rem)==divmod(elapsed*step+frac,den)
 frames=rng.randint(1,3*1024*1024)
 q0=frames*den//step
 rem0=(frames*den-q0*step)&mask
 if rem0>=frac:delay=q0+(rem0-frac+step-1)//step
 else:delay=q0-(frac-rem0)//step
 assert delay==(frames*den-frac+step-1)//step
 assert (delay-1)*step+frac<frames*den<=delay*step+frac
 shift2=rng.randint(80000,120000);f2=frac*shift2//shift
 assert 0<=f2<N*shift2
 assert 0<=Fraction(frac,den)-Fraction(f2,N*shift2)<Fraction(1,N*shift2)
for rate in (44100,48000,88200,96000,176400,192000,384000,768000):
 for shift in (80000,100000,120000):
  den=N*shift;step=rate*100000;total=0;frac=0;elapsed=0
  for i in range(10000):
   ns=rng.randint(1,5000000);elapsed+=ns
   frames,frac=divmod(ns*step+frac,den);total+=frames
  assert (total,frac)==divmod(elapsed*step,den)
print(json.dumps({'status':'PASS','random_exact_arithmetic_cases':100000,'accumulation_sequences':24,'updates_per_sequence':10000,'scope':'userspace mathematical model of rational accounting, modular remainders and deadline ceil; not kernel execution or concurrency validation'}))
