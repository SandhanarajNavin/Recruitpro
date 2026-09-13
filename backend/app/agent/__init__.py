"""The recruiter assistant: a tool-using chat layer over the existing services.

Sits *above* the pipeline. It calls the same services the REST API calls, so a score
it reports came from the same deterministic scoring engine — the assistant reads and
acts, it never computes.
"""
