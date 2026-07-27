# Filter recall by explicit audience before rendering

`recall_structured()` requires an explicit Agent Private or Public audience and applies visibility filtering while assembling the Recall Result, not as a best-effort Renderer convention. Agent Private results may contain internal monologue, persona authority, and private relationship context for model prompting, while Public results exclude those materials; the legacy `recall()` facade is explicitly an Agent Private prompt path, and an Agent Private result cannot be repurposed through a Public Renderer.
