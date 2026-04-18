"""
analytics library for simple curve bootstrapping and instrument pricing 

bootstraps a discount curve from deposits (short end), fras (belly) and swaps (long end) 
pricing vanilla irs (fixed vs floating)

"""

import numpy as np
from datetime import datetime, timedelta
from scipy.optimize import brentq


### market data ###
#deposits for 1M to 6M
deposits = { # act/360 (T in years) : rate
    1/12:  0.0530,
    3/12:  0.0535,
    6/12:  0.0520
}
#fras for 6^M to 2Y
fras = { # act/360 (T1, T2) : rate
    (6/12, 9/12): 0.0525,
    (9/12, 12/12): 0.0520,
    (12/12, 15/12): 0.0515,
    (15/12, 18/12): 0.0510,
    (18/12, 21/12): 0.0505,
    (21/12, 24/12): 0.0500
}
#swaps from 3Y to 60Y
swaps = { # act/360 (T in years) : par_rate
    3:  0.0475,
    4:  0.0460,
    5:  0.0450,
    7:  0.0440,
    10: 0.0435,
    15: 0.0430,
    20: 0.0425,
    30: 0.0428,
    40: 0.0432,
    50: 0.0425,
    60: 0.0420
}

class Curve:
    """
    bootstrapped dfs at pillar dates and uses log-linear interpolation between them.
    
    """

    def __init__(self):
        self._node = [0.0] # {T: df}, T=0 anchor
        self._logDF = [0.0] # log(df) for interpolation, ln(DF(0)) = 0

    def discountFactor(self, T):
        if T < 0:
            raise ValueError("Negative maturity is not allowed.")
        if T == 0:
            return 1.0
        # Interpolate logDF for any T > 0
        return np.exp(float(np.interp(T, self._node, self._logDF)))
        
    def zeroRate(self, T):
        if T < 0:
            raise ValueError("Negative maturity is not allowed.")
        if T == 0:
            return 0.0
        return -np.log(self.discountFactor(T)) / T #continuously compounded zero rate = -ln(DF) / T 

    def fwdRate(self, T1, T2):
        #fwd rates are derived from the df ratio: fwd(t1, t2) = (df(t1)/df(t2) - 1) / (t2 - t1)
        if T1 < 0 or T2 < 0:
            raise ValueError("Negative maturity is not allowed.")
        elif T1 >= T2:
            raise ValueError("T1 must be less than T2.")
        return (self.discountFactor(T1) / self.discountFactor(T2) - 1) / (T2 - T1) #fwd rate from df ratio

    def add_node(self, T, df):
        self._node.append(T)
        self._logDF.append(np.log(df)) #add bootstrapped node


class Instrument:
    def bootstrap(self, curve):
        raise NotImplementedError("Bootstrap method must be implemented by each inst type class")
    
class Deposit(Instrument):
    """
    Deposit instrument for bootstrapping the short end of the curve
    DF(T) = 1 / (1 + r * T)
    assuming act/360
    """
    def __init__(self, mtty, depRate):
        self.T = mtty
        self.depRate = depRate

    def bootstrap(self, curve):
        curve.add_node(self.T, 1 / (1 + self.depRate * self.T))

class FRA(Instrument):
    """
    FRA instrument for bootstrapping the belly of the curve
    DF(T2) = DF(T1) / (1 + r * (T2 - T1)) , given DF(T1) already exists on the curve
    assuming act/360
    """
    def __init__(self, T1, T2, fraRate):
        self.T1 = T1
        self.T2 = T2
        self.fraRate = fraRate

    def bootstrap(self, curve):
        df_T1 = curve.discountFactor(self.T1)
        df_T2 = df_T1 / (1 + self.fraRate * (self.T2 - self.T1))
        curve.add_node(self.T2, df_T2)

class Swap(Instrument):

    def __init__(self, mtty, parRate, fixedFreq=2):
        self.T = mtty
        self.parRate = parRate
        self.fixedFreq = fixedFreq
    """
    def bootstrap(self, curve):
        dt= 1.0 / self.fixedFreq
        fixedTimes = np.arange(dt, self.T + 1e-9, dt) #generates payment dates up to mtty
        annuity = sum(curve.discountFactor(t) for t in fixedTimes[:-1]) #sum of dfs for all coupon dates except the last
        newDf = (1 - self.parRate * dt * annuity) / (1 + self.parRate * dt) #solve for the unknown df at mtty
        curve.add_node(self.T, newDf)
    """
    
    def bootstrap(self, curve):
        dt_fix = 1.0 / self.fixedFreq
        dt_flt = 1.0 / 4   # match pricer float frequency
        fix_times = np.arange(dt_fix, self.T + 1e-9, dt_fix)
        flt_times = np.arange(dt_flt, self.T + 1e-9, dt_flt)

        def residual(df_T):
            # temporarily add the pillar so interpolation works for the final period
            curve._node.append(self.T)
            curve._logDF.append(np.log(df_T))

            annuity  = sum(curve.discountFactor(t) for t in fix_times)
            pv_fixed = self.parRate * dt_fix * annuity + df_T   #fixed leg + notional
            pv_float = sum(
                curve.fwdRate(t - dt_flt, t) * curve.discountFactor(t) * dt_flt
                for t in flt_times
            ) + df_T   #float leg + notional

            #remove temp pillar
            curve._node.pop()
            curve._logDF.pop()

            return pv_fixed - pv_float

        df_new = brentq(residual, 1e-6, 1.0) ## use root finding algorithm to solve for the df that makes the swap PV zero (par rate condition)
        curve.add_node(self.T, df_new)
    


# helper function to build the curve
def buildCurve(deposits, fras, swaps):

    curve = Curve()

    inst = (
        [Deposit(T,r) for T, r in sorted(deposits.items())] +
        [FRA(T1, T2, r) for (T1, T2), r in sorted(fras.items())] +
        [Swap(T, r) for T, r in sorted(swaps.items())]
    )

    for i in inst:
        i.bootstrap(curve)

    return curve

# helper function to bump the curve mkt data
# only parallel bumps implemented
def bump_market_data(deposits, fras, swaps, bump_bps):
    bump = bump_bps / 10000

    d = {k: v + bump for k, v in deposits.items()}
    f = {k: v + bump for k, v in fras.items()}
    s = {k: v + bump for k, v in swaps.items()}

    return d, f, s

# helper function to generate range of dates
def date_range(start:datetime.date, end:datetime.date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)

# helper function to generate historical market data
def historical_market_data(base_deposits, base_fras, base_swaps, day_index):


    drift = day_index * 0.00002 # each day rates move with upward trend of 2bps 
    noise = np.random.uniform(-0.0001, 0.0001) # random noise between -1bp and +1bp

    shift = drift + noise

    d = {k: v + shift for k, v in base_deposits.items()}
    f = {k: v + shift for k, v in base_fras.items()}
    s = {k: v + shift for k, v in base_swaps.items()}

    return d, f, s

#### PRICING FUNCTIONS ####

def priceSwap(curve, mtty, fixedRate, notional, payOrReceive, fixedFreq=2, floatFreq=4, ):
   
   dtFixed = 1.0 / fixedFreq
   fixedTimes = np.arange(dtFixed, mtty + 1e-9, dtFixed) 
   dtFloat = 1.0 / floatFreq
   floatTimes = np.arange(dtFloat, mtty + 1e-9, dtFloat)

   annuity = sum(curve.discountFactor(t) for t in fixedTimes) #sum of dfs for fixed leg payment dates
   pvFixed = notional *fixedRate * dtFixed * annuity #PV of fixed leg
   pvFloat = notional *dtFloat * sum(curve.fwdRate(t - dtFloat, t) * curve.discountFactor(t) for t in floatTimes) #PV of floating leg using forward rates

   parRate = pvFloat / (notional * dtFixed * annuity) #par rate for the swap

   if payOrReceive == "pay":
        pv = pvFloat - pvFixed #net value to the fixed rate payer
   elif payOrReceive == "receive":
        pv =  pvFixed - pvFloat #net value to the fixed rate receiver
   else:
        raise ValueError(f"payOrReceive must be 'pay' or 'receive', got '{payOrReceive}'")
   

   dv01 = notional* dtFixed * annuity * 0.0001 #dv01 = change in PV for 1 basis point change in fixed rate

   return {"pv": pv, "parRate": parRate, "dv01": dv01}
    
if __name__ == "__main__":

    curve = buildCurve(deposits, fras, swaps)


    print(priceSwap(curve, mtty=3,  fixedRate=0.0475, notional=1000000, payOrReceive="receive"), #at par PV approx 0 (-410 on 10M notional)
    priceSwap(curve, mtty=5,  fixedRate=0.0460, notional=1000000, payOrReceive="pay"),
    priceSwap(curve, mtty=10, fixedRate=0.0440, notional=1000000, payOrReceive="receive"))
"""
    #get dates and iterate over them
    for i, d in enumerate(date_range(datetime.date(2026,2,20), datetime.date(2026,2,26))):
        # gen historical mkt data for the date
        #print(i, d)
        h_dep, h_fra, h_swp = historical_market_data(
            deposits, fras, swaps, i
        )

        curve = buildCurve(h_dep, h_fra, h_swp)

        res = priceSwap(curve, mtty=10, fixedRate=0.0440, notional=1000000, payOrReceive="receive")
            
"""