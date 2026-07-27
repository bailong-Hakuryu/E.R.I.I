# Separate structured recall from prompt rendering

E.R.I.I. will add `recall_structured()` as the renderer-neutral structured recall capability while retaining `recall()` as the string-returning compatibility facade backed by the default Renderer. This preserves existing host integrations without forcing new hosts to parse Markdown, and keeps presentation choices outside Recall Result semantics.
