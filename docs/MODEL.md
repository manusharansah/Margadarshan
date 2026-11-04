# Model evidence

Each number the planner uses falls into one of four groups: taken from a published source, derived from physics,
learned from the real network data, or assumed. This page says which group each belongs to and what was checked.
Nothing here has been validated against GPS traces or fuel logs from Nepali trucks, because none were available.

## Summary

| Item | Basis | Checked how | Status |
|---|---|---|---|
| Rainfall threshold for landslides | Published: Dahal and Hasegawa 2008, I = 73.9 D^-0.79 | Formula reproduces the quoted 144 mm per 24 h; unit tests | Source read in summary form, full paper not read |
| Hazard from terrain, incidents | My own screening score | Tests of the arithmetic only | Not calibrated. Landslide catalogues were unreachable. |
| Climbing speed | Physics: engine power against rolling, air and grade resistance | Hand calculation in tests; crawl speed table below | No field data |
| Weight-to-power ratio 120 kg/kW | Design value used in highway engineering guidance | Not checked against Nepali fleets | Assumption with a source |
| Curve speed | Standard lateral-friction relation, V = sqrt(127 R (e + f)) | Tests against the formula | Applied to the average radius of a 0.5 km piece, which overstates speed on hairpins |
| Fuel | Energy balance with assumed efficiencies | Hand calculation in tests; order-of-magnitude check below | No Nepali truck data found |
| Surface for untagged roads | Learned from tagged roads of the same class | Held-out accuracy 83.6% against 72.8% for my earlier defaults, on 7,057 pieces | Tagged roads may not represent untagged ones |
| One-way roads | Read from the road data | 4.5% of pieces are one-way; 3 stranded junctions dropped | Data quality is the source's |
| Free-flow speed by road class | Planning defaults | Covered by the uncertainty simulation | Assumption |
| Loading time per stop | No published Nepal figure found | An organisation setting, default 0 | Needs your own timings |
| Traffic and closures | No public live source | Manual closures per organisation; congestion enters the uncertainty simulation | Not modelled live |
| ETA uncertainty | Monte Carlo over speed, engine power and congestion | Tests of the draws and the combination rules | The assumed spreads are mine |

## Rainfall

Dahal and Hasegawa (2008) analysed 677 landslides in Nepal between 1951 and 2006, used 193 with rainfall records, and gave
the lower boundary of triggering rainfall as I = 73.9 D^-0.79, with I in mm per hour and D in hours. Multiplying by D
gives an accumulated depth of 73.9 D^0.21 mm: 144 mm in 24 hours, 181 mm in 72 hours. The rain index for a 10 km grid cell
is the largest ratio of observed rolling rainfall to that threshold over 24, 48 and 72 hour windows, capped at 1. It
replaces my earlier arbitrary "rainfall divided by 200 mm". Open-Meteo supplies the hourly series for the past three days
and the next day. The threshold is a regional lower bound for onset, so an index near 1 means "conditions seen before
landslides here", not a forecast of one.

## Truck speed

Uphill speed solves the power balance (Crr m g + m g grade + 0.5 rho CdA v^2) v = engine power times driveline efficiency.
Engine power is mass divided by a weight-to-power ratio. The truck-speed-profile literature uses this framework and
reports weight-to-power as the main driver of grade performance. For an 8 tonne truck at 120 kg per kW (66.7 kW):

| Grade | 0% | 2% | 4% | 6% | 8% | 10% | 12% |
|---|---|---|---|---|---|---|---|
| Steady speed, paved (km/h) | 84 | 63 | 46 | 35 | 28 | 23 | 20 |

Downhill speed is capped for braking safety from a 5% grade, falling 2.5 km/h per extra percent to a floor of 15 km/h.
That cap is my assumption. Curvature comes from the cumulative heading change of each piece's geometry, and the curve limit
uses V = sqrt(127 R (e + f)) with e + f = 0.19. Published work finds drivers' speed follows a curvature index more
closely than individual curve design speeds, but I did not retrieve its regression coefficients, so the physical
relation stands in for them.

On the real network the model gives Kathmandu to Banepa in 36 minutes for 26.4 km, and Dolalghat to Dhulikhel, a
climb of about 800 m, in 59 minutes against 54 minutes coming down.

## Fuel

Mechanical energy is rolling resistance plus aerodynamic drag plus the net climb, never below zero, divided by driveline
efficiency 0.85 and engine efficiency 0.35, with 36 MJ per litre of diesel, plus 2.5 L per hour of driving for idle and
accessories. An 8 tonne truck on flat paved road at 40 km/h uses about 17 L per 100 km (5.9 km/L). On the climb from
Dolalghat to Dhulikhel it uses 13.9 L against 6.0 L going down. The old formula had no unit and no direction.

I found no Nepal truck figure to compare with. For a sanity check, a Swedish study of loaded logging trucks measured 71 to
91 L per 100 km on mixed roads. The same model run for a 60 tonne truck on flat road at 70 km/h gives 62 L per 100 km,
lower as expected because it leaves out hills and gravel.

## Surface

Overture tags a surface on about 24% of road pieces in the study area. For the rest, the earlier code guessed from the road
class. The new code uses the share of paved, gravel and rough surfaces among tagged roads of the same class. Hiding 30% of
the tagged roads at random, 20 times, the learned prior predicts the hidden surface correctly 83.6% of the time against
72.8% for the old defaults. Costs, times and fuel use the probabilities as expectations, which matters for class
"unclassified", where 41% of tagged roads are paved and 51% are rough.

## ETA uncertainty

Each plan's route is timed again 300 times with random draws: free-flow speed scale (lognormal, sigma 0.20), engine power
(plus or minus 20%) and a congestion factor (1.00 to 1.30 on driving time). Parallel legs share each draw. The page shows the
10th to 90th percentile and the share of draws that miss the deadline. These spreads are my assumptions, so the range is
an honest statement of how little the inputs pin down the answer, not a measured distribution. With GPS traces you can
replace the spreads with measured ones in `gis_backend/uncertainty.py`.

## What would make this validated

1. GPS traces or driver logs for 20 or more real trips, to fit speeds and congestion.
2. Fuel receipts and odometer readings for the same trips, to fit the efficiencies.
3. A landslide and road-blockage catalogue with dates and places, such as a BIPAD export, to calibrate the hazard score.
   NASA's Global Landslide Catalog and BIPAD were unreachable from the build environment.
4. Timed loading and unloading at your warehouses, for the handling time.

## Sources

* Dahal and Hasegawa (2008), Representative rainfall thresholds for landslides in the Nepal Himalaya, Geomorphology 100:429.
  https://ui.adsabs.harvard.edu/abs/2008Geomo.100..429D/abstract
* Truck speed profile and weight-to-power: FHWA truck size and weight study working paper
  https://www.fhwa.dot.gov/reports/tswstudy/TSWwp6.pdf and the Argentine field study
  https://trid.trb.org/View/471121
* Road geometry and fuel: Svenson, impact of road geometry and surface roughness on fuel consumption
  https://hvttforum.org/wp-content/uploads/2019/11/Svenson-The-Impact-of-Road-Geometry-and-Surface-Roughness-on-Fuel-Consumption.pdf
* Curvature index and operating speed: https://dl.lib.uom.lk/items/bad4591f-b2c9-4c88-887a-adea56027c03 and a mountain freeway
  simulator study https://tjsafety.tongji.edu.cn/5d/d6/c36029a351702/page.htm
* NASA Global Landslide Catalog (not used, see above): https://www.un-spider.org/links-and-resources/data-sources/global-landslide-catalog-glc
