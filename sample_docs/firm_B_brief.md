# Firm B — Method Variant (same fund, same holdings)

Firm B administers the **same** Meridian Fixed Income Fund using the **same**
`sample_fund_guidelines.pdf` and the **same** `sample_holdings.csv`, but applies
three house conventions that differ from the default reading. Your system must
reproduce Firm B's expected figures **by configuration only** — without changing
engine code.

## Firm B's three house conventions
1. **Aggregate non-IG exposure includes "fallen angels."** Any holding whose
   current rating is below investment grade (BB+ or lower) counts toward the
   non-IG aggregate, **even if its asset class is "Investment Grade Corporate
   Bonds."** (Use the `credit_rating` / `downgraded_from` columns.)
2. **GRE concentration is measured at the parent issuer.** Government-related
   entities sharing a `parent_issuer` are aggregated and tested against the 12%
   GRE cap as a single group.
3. **Utilization is reported in truncated basis points**, not a 1-decimal
   percentage (e.g., 58.333% → `5833 bps`).

## Firm B expected answer key (the figures that DIFFER from Firm A)
| Metric | Firm A | Firm B | Why it differs |
|---|---|---|---|
| Aggregate non-IG exposure | 15.0% — OK | **21.0% — BREACH** | + Marina Bay Resorts (BB, was BBB-) 6% |
| Largest GRE issuer | 7.0% — OK | **13.0% — BREACH** | Redhill Power 7% + Redhill Transport 6% grouped under Redhill Holdings |
| Utilization representation | `58.3%` etc. | `5833 bps` etc. | truncated-bps house style |

All other figures (allocations, single corporate issuer, liquidity, duration,
DV01) are identical to Firm A.
