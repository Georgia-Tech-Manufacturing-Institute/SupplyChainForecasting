

NB: currently, item number/part numbers of length <8 or containing spaces are deleted. This much be changed in the loader to accept these values. 

# Expected File Formats

```
Site|Item Number|Trans Date|Trans Number|Trans Type|Trans Qty|Trans UM|Loc Qty Chg|Location|End Balance
AT00|L0413582AA04600|07/27/26|41464721|ISS-WO|-10|EA|-10|RAW|1522
AT00|L0413592AA02600|07/27/26|41464722|ISS-WO|-10|EA|-10|WIP|524
```


sdfasd

## Expected Demand data format 

Demand data files are anticipated to be pipe-deliminated and contain the following columns:

```
Ship-From|Description|Order|Line|Customer|Customer Sort|Ship-To|Name|Week#|Rel Date|SO|Ln|ShipTo|Customer Part|Part|PCR|00/00/00|...
```

The column 00/00/00 represents a given Month/Day/Year date for the 


```
Ship-From|Description|Order|Line|Customer|Customer Sort|Ship-To|Name|Week#|Rel Date|SO|Ln|ShipTo|Customer Part|Part|PCR|08/03/26|08/10/26|08/17/26|08/24/26|08/31/26|09/07/26|09/14/26|09/21/26|09/28/26|10/05/26|10/12/26|10/19/26|10/26/26|11/02/26|11/09/26|11/16/26|11/23/26|11/30/26|12/07/26|12/14/26|12/21/26|12/28/26|01/04/27|01/11/27
AT00|IAC Group - Arlington|GMA139|8|01008607|DAKKOTA INTEGRATED SYSTEMS|01009399|INTIER-KELLER ROAD WAREHOUSE|32|08/03/26|GMA139|8|01009399|84824532|84824532|6|0|0|0|1|0|0|0|0|0|1|0|0|0|1|0|0|0|0|1|0|0|0|0|0
AT00|IAC Group - Arlington|GMA139|29|01008607|DAKKOTA INTEGRATED SYSTEMS|01009399|INTIER-KELLER ROAD WAREHOUSE|32|08/03/26|GMA139|29|01009399|84824550|84824550|26|0|0|1|0|1|1|0|1|0|1|0|1|1|2|0|0|0|0|0|0|0|0|0|0 
```

## Expected Order 

# Notes on parsing
The final format of the prediction dataset is in the form of a unique (partnumber, orderidx, predidx) triple, where the idx is an integer week index. That is, there can only be one forecast quantity for a given week; new information needs to be well understood as 

## Filtering

Filtering is a delicate balance in this dataset. Data is filtered at different points and within different contexts. The cleaning process filters out data which is unlikely to be useful or pertinent. For example, part numbers which are never seen in the customer demand documents are filtered prior to SQL ingestion; while there may be future features which could make use of it, 

The full extent data is important for data features; information like rolling values


## Basic parsed row format



| column     | meaning                                                                 |
|------------|--------------------------------------------------------------------------|
| `Part`     | part number, cast to `str`                                              |
| `predqty`  | (`pq`) predicted/ordered quantity for that `OrderDate`, from the melted value column |
| `predidx`  | (`pi`) week index of the prediction — `RelDate`'s ISO year combined with the original `Week#` column, via `make_week_idx` |
| `orderidx` | (`oi`) week index of the order — `OrderDate`'s ISO year/week, via `make_week_idx` |

`CustomerPart`, `SO`, `Ln`, the raw `Week#`, `OrderDate`, and the intermediate
`Lookahead`/`predyear`/`predweek`/`orderyear`/`orderweek` columns are all
dropped once `predidx`/`orderidx` are computed. The result is
deduplicated (`drop_duplicates`), so a given
(`Part`, `orderidx`, `predidx`) combination appears once.

Two other parsers in this file produce simpler, single-purpose row formats
from the same `stateParse`/`parse_fixed_width` fixed-width reader in
[processor.py](processor.py):

- `consumpParse` — one row per `Part` per week: `Part`, `Quantity1` (absolute
  value, thousands-separators stripped), and `orderidx` (`oi`), built from
  the `year`/`week` arguments passed in rather than parsed from the file.
- `costPrep` — one row per `Part`: `Part`, `Start` (effective date), `Amt1`
  (unit cost). Rows are collapsed to the most recent `Start` per part
  (`groupby('Part')['Start'].idxmax()`), so there is exactly one active cost
  row per part.

## Data assumptions

