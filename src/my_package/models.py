from pydantic import BaseModel, Field, field_validator
from typing import Literal
from datetime import date

class SwapInstrumentRequest(BaseModel):
    maturity: float = Field(description="Maturity in years")
    fixedRate: float = Field(description="Fixed rate")
    notional: float = Field(default=1000000.0, description="Notional amount")
    payOrReceive: Literal["pay", "receive"] = Field(description="Whether to pay or receive fixed rate")
    fixedFreq: int = Field(default=2, description="Fixed frequency (default pay semi-annually)")
    floatFreq: int = Field(default=4, description="Float frequency (default quarterly reset floating leg)")

    @field_validator("maturity")
    @classmethod
    def check_maturity(cls, m):
        if m <= 0:
            raise ValueError("Maturity must be positive")
        return m 


class CurveBumpRequest(SwapInstrumentRequest):
    bump_bps: float = Field(default=1.0, description="Bump in basis points") #if user does not specify, default to 1 bp shift,
    #which essentially makes the endpoint a DV01 calculator, but it also remains a scenario pricer (at larger shifts eg 50bp)


class SwapTimeseriesRequest(SwapInstrumentRequest):
    start_date: date = Field(description="Start Date for timeseries")
    end_date: date = Field(description="End Date for timeseries")

class Explanation(BaseModel):
    summary: str #llm can use/ return directly without haveing to make any interoretation therefore reducing hallucination risk
    model: str        
    curveUsed:str       
    assumptions: list[str]   