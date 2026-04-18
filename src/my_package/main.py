
from fastapi import FastAPI

from .an_lib import Curve, buildCurve, priceSwap, bump_market_data, historical_market_data,date_range, deposits, fras, swaps
from .models import SwapInstrumentRequest, CurveBumpRequest, SwapTimeseriesRequest, Explanation

app = FastAPI(title="Swap Pricing API for LLM integration")


### API endpoints ###

##Endpoint 0: check that it is running
@app.get("/")
def root():
    return {"API is running please use /docs to be directed to sweagger api to interract with the endpoints"}

##Endpoint 1: Description
@app.post("/instrument/info")
def instrument_info(req: SwapInstrumentRequest):
    return {
        "instrument": {
            "type": "interest_rate_swap",
            "maturity": req.maturity,
            "fixedRate": req.fixedRate,
            "notional": req.notional,
            "payOrReceive": req.payOrReceive,
            "fixedFreq": req.fixedFreq,
            "floatFreq": req.floatFreq,
        },
        "resolved_assumptions": [
            "act/360",
            "vanilla fixed vs float",
            "curve bootstrapped from deposits for short end, FRAs for belly & swaps for long end"
        ]
    }

## Endpoint 2: Price swap
@app.post("/instrument/pricing")
def price_swap_endpoint(req: SwapInstrumentRequest):

    curve = buildCurve(deposits, fras, swaps)
    #curve will be rebuilt for every request (limitation of this example - in prod it would be usually cached and rebuilt on mkt data updates)

    result = priceSwap(
        curve,
        mtty=req.maturity,
        fixedRate=req.fixedRate,
        notional=req.notional,
        payOrReceive=req.payOrReceive,
        fixedFreq=req.fixedFreq,
        floatFreq=req.floatFreq,
    )
    above_below = "above" if req.fixedRate > result["parRate"] else "below"
    direction = "losing" if req.payOrReceive == "pay" and req.fixedRate > result["parRate"] else "gaining"

    return {
        "instrument": req.model_dump(),
        "curve": "base",
         "results": {
            "pv": result["pv"],
            "par_rate": result["parRate"],
            "dv01": result["dv01"]
        },
        "measure_definitions": {
            "pv": "present value of swap",
            "par_rate": "fixed rate that makes PV = 0",
            "dv01": "change in PV for 1bp change in fixed rate"
        },
        "explain": Explanation(
            summary=(
                f"This {req.maturity:.0f}Y {req.payOrReceive}-fixed swap has a PV of "
                f"{result['pv']:,.2f}. The fixed rate ({req.fixedRate:.2%}) is "
                f"{above_below} the par rate ({result['parRate']:.2%}), "
                f"so the payer is currently {direction}."
            ),
            model="discounted_cashflow_swap",
            curveUsed="bootstrapped SOFR curve: deposits (short end) + FRAs (belly) + swaps (long end)",
            assumptions=["act/360", "single-curve", "no CVA or FVA", "notional not exchanged"],
            caveats=["curve uses static hardcoded market data, not live rates",
                     "dv01 is analytical approximation, not full bump-and-reprice"],
        ).model_dump()
    }

# Endpoint 3: Price swap with bumped curve
@app.post("/instrument/pricing/bumped")
def price_swap_bumped(req: CurveBumpRequest):

    # base curve
    base_curve = buildCurve(deposits, fras, swaps)

    base = priceSwap(
        base_curve,
        mtty=req.maturity,
        fixedRate=req.fixedRate,
        notional=req.notional,
        payOrReceive=req.payOrReceive,
        fixedFreq=req.fixedFreq,
        floatFreq=req.floatFreq,
    )

    # bumped curve
    d, f, s = bump_market_data(deposits, fras, swaps, req.bump_bps)
    bumped_curve = buildCurve(d, f, s)

    bumped = priceSwap(
        bumped_curve,
        mtty=req.maturity,
        fixedRate=req.fixedRate,
        notional=req.notional,
        payOrReceive=req.payOrReceive,
        fixedFreq=req.fixedFreq,
        floatFreq=req.floatFreq,
    )

    return {
        "instrument": req.model_dump(),
        "scenario": f"+{req.bump_bps}bps parallel",
        "base": base,
        "bumped": bumped,
        "sensitivity": {
            "pv_change": bumped["pv"] - base["pv"] 
        },"explain": Explanation(
            summary=(
                f"Bumping the curve up by {req.bump_bps}bps changes the swap PV from "
                f"{base['pv']:,.2f} to {bumped['pv']:,.2f}, a change of {bumped['pv'] - base['pv']:,.2f}."
            ),
            model="discounted_cashflow_swap priced with bumped curve",
            curveUsed="bootstrapped SOFR curve: deposits (short end) + FRAs (belly) + swaps (long end)",
            assumptions=["act/360", "single-curve", "no CVA or FVA", "notional not exchanged"],
            caveats=["curve uses static hardcoded market data, not live rates",
                     "bump is parallel shift of all rates, in reality different tenors may move differently"],
        ).model_dump()
    }

# Endpoint 3: Generate timeseries of swap rates and pv between 2 dates
@app.post("/instrument/pricing/timeseries")
def swap_rate_timeseries(req: SwapTimeseriesRequest):

    results = []

    #get dates and iterate over them
    for i, d in enumerate(date_range(req.start_date, req.end_date)):
        # gen historical mkt data for the date
        h_dep, h_fra, h_swp = historical_market_data(
            deposits, fras, swaps, i
        )

        curve = buildCurve(h_dep, h_fra, h_swp)

        res = priceSwap(
            curve,
            mtty=req.maturity,
            fixedRate=req.fixedRate,
            notional=req.notional,
            payOrReceive=req.payOrReceive,
            fixedFreq=req.fixedFreq,
            floatFreq=req.floatFreq,
        )

        results.append({
            "date": d,
            "par_rate": res["parRate"],
            "pv": res["pv"]
        })

    return {
        "instrument": req.model_dump(),
        "series_type": "swap_par_rate_timeseries",
        "points": results,
        "explain": Explanation(
            summary=(
                f"This timeseries shows how the par rate and PV of the swap evolved between "
                f"{req.start_date} and {req.end_date} based on historical market data."
            ),
            model="discounted_cashflow_swap priced with historical curves",
            curveUsed="bootstrapped SOFR curve: deposits (short end) + FRAs (belly) + swaps (long end)",
            assumptions=["act/360", "single-curve", "no CVA or FVA", "notional not exchanged"],
            caveats=["historical market data is randomly generated for this example, not actual historical rates",
                     "in a real implementation, would need to handle missing data and non-business days"],
        ).model_dump()
    }