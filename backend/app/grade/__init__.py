"""Script assembly + auto colour correction + reference-image grades.

Local/free only. Pure-numpy colour maths (``cube``, ``colorspace``, ``correct``,
``looks``), ffmpeg sampling/rendering (``sampling``, ``grid``) and the
orchestrator (``job``) that hands results to Resolve Free via the existing
queue (``edits/resolve_assembly.py`` runs inside Resolve).
"""
