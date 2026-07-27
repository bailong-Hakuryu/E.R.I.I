# Structured recall is read-only by default

`recall_structured()` does not reinforce selected memories unless the host explicitly requests reinforcement, and rendering a Recall Result never writes to storage. The legacy `recall()` facade retains automatic reinforcement for compatibility; this separates actual conversational recall from preview, evaluation, retry, and re-rendering so those operations cannot silently reshape future salience.
