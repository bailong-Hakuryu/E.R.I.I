# Default rendering reuses stored persona reflections

The default Prompt Renderer is deterministic and does not call an LLM to invent character-specific wording during recall. It presents Character Blueprint context, accepted Persona Reflections, relevant events, and qualitative relationship direction while keeping internal numeric state out of the default prompt; the conversation Agent produces the final in-character wording, and any custom generated rendering remains transient unless it later passes through the normal evidence and adjudication path.
