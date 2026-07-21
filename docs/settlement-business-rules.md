# Settlement Business Rules — three categories, one formula

Three different business models share one settlement formula. Only the way
**Guaranteed Amount** and **Market Amount** are derived changes.

```
Difference = Guaranteed Amount − Market Amount

Difference > 0  → Seller pays Client
Difference < 0  → Client pays Seller
Difference = 0  → nothing is payable
```

| Category       | Guaranteed Amount          | Market Amount                             |
| -------------- | -------------------------- | ----------------------------------------- |
| IPO Allotment  | Applications × Buy Rate    | Allotted Shares × Listing Sold Price      |
| Subject 2      | Allotted Shares × Buy Rate | Allotted Shares × Allotment Sold Price    |
| Premium        | Shares × Buy Rate          | Shares × Sell Rate                        |

---

## 1. IPO Allotment (Application Deal)

The client pays per **application**, regardless of whether it is allotted. Only
allotted applications produce shares, and those shares are sold at the listing
price.

- **Guaranteed** = Applications × Buy Rate
- **Market** = Allotted Applications × Shares Per Application × Listing Sold Price

### Example

| Input                  | Value      |
| ---------------------- | ---------- |
| Applications           | 5          |
| Buy Rate               | ₹1,000     |
| Allotted applications  | 2          |
| Shares per application | 364        |
| Listing sold price     | ₹8         |

- Guaranteed = 5 × 1,000 = **₹5,000**
- Market = (2 × 364) × 8 = 728 × 8 = **₹5,824**
- Difference = 5,000 − 5,824 = **−₹824** → **Client pays ₹824**

### In code

[settlement.py:188-192](../utils/ipo/settlement.py#L188-L192) — one settlement line
per allotment row (one row = one application):

- `vyaj` = `cost_per_app` = `buy_amt / buy_app` = the Buy Rate
  ([allotments.py:102-106](../utils/ipo/allotments.py#L102-L106); `buy_amt` is
  stored as `buy_app × buy_rate`, [service.py:510](../utils/ipo/service.py#L510)),
  so summing across N application rows gives `Applications × Buy Rate`.
- `shares_allotted` is forced to 0 unless `status == "Allotted"`
  ([settlement.py:155](../utils/ipo/settlement.py#L155)).
- `sell_amt` = `shares_allotted × sold_price` = the Market leg.
- `net_pl` = `sell_amt − vyaj` = **Market − Guaranteed** = −Difference.

## 2. Subject 2 (Share Guarantee Deal)

Nothing settles unless there is an allotment. The client keeps the market sale
proceeds; only the gap to the guarantee moves.

- **Guaranteed** = Allotted Shares × Buy Rate
- **Market** = Allotted Shares × Allotment Sold Price (the price actually
  recorded on the allotment row — never a Position or Sell Trade sell rate)

### Example

| Input          | Value  |
| -------------- | ------ |
| Allotted shares| 364    |
| Buy Rate       | ₹84.5  |
| Sold price     | ₹39.3  |

- Guaranteed = 364 × 84.5 = **₹30,758**
- Market = 364 × 39.3 = **₹14,305.20**
- Difference = 30,758 − 14,305.20 = **₹16,452.80** → **Seller pays Client ₹16,452.80**

### In code

[settlement.py:158-187](../utils/ipo/settlement.py#L158-L187):

- `guaranteed` = `shares_allotted × position.buy_rate`
- `sell_amt` = `shares_allotted × allotment.sold_price`
- `net_pl` = `sell_amt − guaranteed` = **Market − Guaranteed** = −Difference
- No allotment ⇒ every leg is 0. Allotted but no sold price recorded ⇒
  `s2_pending = True`, `net_pl = 0`, and a preview warning is raised
  ([settlement.py:676-681](../utils/ipo/settlement.py#L676-L681)).

## 3. Premium (Direct Share Deal)

There is no allotment — the share quantity is fixed at trade time.

- **Guaranteed** = Shares × Buy Rate
- **Market** = Shares × Sell Rate

### Example

| Input     | Value   |
| --------- | ------- |
| Shares    | 1,000   |
| Buy Rate  | ₹12     |
| Sell Rate | ₹13     |

- Guaranteed = 1,000 × 12 = **₹12,000**
- Market = 1,000 × 13 = **₹13,000**
- Difference = 12,000 − 13,000 = **−₹1,000** → **Client pays ₹1,000**

### In code — settled gross, on two legs

Premium is the one category the system does **not** net into a single line. It
books both legs at full value:

- **Buy leg** ([settlement.py:347-402](../utils/ipo/settlement.py#L347-L402)):
  `vyaj = buy_amt` (= Shares × Buy Rate = Guaranteed), `sell_amt = 0`,
  `net_pl = −buy_amt` → **Payable**: the buy party is owed the full guaranteed
  amount.
- **Sell leg** ([settlement.py:106-117](../utils/ipo/settlement.py#L106-L117)):
  `vyaj = market_amount = sell_amt of the sell trade` (= Shares × Sell Rate =
  Market), `net_pl = −seller_vyaj` → **Receivable**: the sell party owes the
  full market amount.
- `brokerage = Market − Guaranteed = Shares × (Sell Rate − Buy Rate)`, which is
  exactly the magnitude of the Difference above.

So the net across the two legs equals the single-line Difference, but the
statement shows ₹12,000 out and ₹13,000 in rather than a net ₹1,000.

---

## Sign convention in the code

The code carries the difference as `net_pl = Market − Guaranteed`, i.e. the
**negative** of the `Difference` in the rules above. The resulting direction is
the same:

| Rules                   | `net_pl`  | Buy-side `direction` |
| ----------------------- | --------- | -------------------- |
| Difference > 0 → Seller pays Client | negative | `Payable`    |
| Difference < 0 → Client pays Seller | positive | `Receivable` |
| Difference = 0 → nothing payable    | 0        | `Settled`    |

`_direction` ([settlement.py:49-52](../utils/ipo/settlement.py#L49-L52)) is the
buy/client side. `_sell_side_direction`
([settlement.py:55-59](../utils/ipo/settlement.py#L55-L59)) inverts it for sell
parties (Ambica, Mama, …), because a positive listing P/L there means we owe
them.

## What the rules above do not cover

The three models describe only the **client (buy) leg**. Settlement also books a
**sell-party leg** per sell trade, driven by the grey-market contract:

- IPO Allotment: sell-party `net_pl = Listing Amount − Grey Sell Amount`;
  `brokerage = Grey Sell Amount − (Sell Qty × Buy Rate)`.
- Subject 2: the *contract* (`Allotted Shares × Sell Trade Rate`) is a third,
  independent price. `net_pl = Market − Contract`,
  `brokerage = Contract − Guaranteed`
  ([settlement.py:73-100](../utils/ipo/settlement.py#L73-L100)).
- Premium: as described above.

Note that the `settlement_difference` field is not uniform across line types —
for Subject 2 it holds `Guaranteed − Market` (the client leg, matching the rules
above), while for IPO Allotment and Premium it holds the brokerage. Read the
`SETTLEMENT DIFFERENCE` column in the Excel export
([settlement.py:1017](../utils/ipo/settlement.py#L1017),
[settlement.py:1053](../utils/ipo/settlement.py#L1053)) with that in mind.
