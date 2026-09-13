"""Search-quality evaluation (architecture doc §27).

Separate from ``services`` because nothing here serves a request: it exists to tell
you whether a change to retrieval, ranking or scoring actually improved matching,
which is otherwise unknowable.
"""
