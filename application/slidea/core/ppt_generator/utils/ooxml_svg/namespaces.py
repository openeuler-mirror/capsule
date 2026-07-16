P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
SVG = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"

NS = {"p": P, "a": A, "r": R, "c": C, "pr": PR}


def qn(namespace: str, local: str) -> str:
    return f"{{{namespace}}}{local}"

