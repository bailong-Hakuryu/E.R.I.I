# Apply recall budgets before rendering

Recall Budget selects complete recall projections before Recall Result is created, using a stable default cost estimate or a host-supplied estimator, and the result reports omitted source identities and reasons. Only projections that enter the result may be reinforced; Renderers must preserve every selected semantic item and raise an explicit budget failure when they cannot express the result within their own declared limit, rather than truncating text or silently dropping memories after recall.
