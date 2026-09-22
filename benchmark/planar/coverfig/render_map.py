"""Render one map of the paper's cell-cover figures, from the job file make_jobs.py writes.

It runs under an interpreter carrying the QGIS Python API, which render.sh names as QGIS_PYTHON:
on Linux the system python3 with python3-qgis installed, on Windows the QGIS bat.

  python3 render_map.py job.json

The job file states the output, its size, the extent and the layers from bottom to top. Land is the
map background and the OSM Shortbread water layers are drawn over it, since Shortbread has no land
layer. The basemap is fetched over the network, so a render needs one.
"""
import json
import sys

from qgis.core import (
    QgsApplication, QgsCoordinateReferenceSystem, QgsRectangle, QgsMapSettings,
    QgsMapRendererParallelJob, QgsRasterLayer, QgsVectorLayer, QgsVectorTileLayer,
    QgsVectorTileBasicRenderer, QgsVectorTileBasicRendererStyle, QgsFillSymbol,
    QgsLineSymbol, QgsMarkerSymbol, QgsSingleBandPseudoColorRenderer,
    QgsColorRampShader, QgsRasterShader, QgsWkbTypes, QgsPalLayerSettings,
    QgsTextFormat, QgsVectorLayerSimpleLabeling, QgsTextBufferSettings,
    QgsCoordinateTransform, QgsProject)
from qgis.PyQt.QtCore import QSize, Qt, QRectF, QPointF
from qgis.PyQt.QtGui import QColor, QImage, QPainter, QFont, QLinearGradient, QPen

SHORTBREAD = ("type=xyz&url=https://vector.openstreetmap.org/shortbread_v1/"
              "{z}/{x}/{y}.mvt&zmin=0&zmax=14")


def shortbread_layer(water, border, land="#f2efe9", streets=None, buildings=None):
    layer = QgsVectorTileLayer(SHORTBREAD, "shortbread")
    styles = []
    for name in ("ocean", "water_polygons"):
        st = QgsVectorTileBasicRendererStyle(name, name, QgsWkbTypes.PolygonGeometry)
        # An outline in the fill colour closes the hairline seam between tiles
        st.setSymbol(QgsFillSymbol.createSimple(
            {"color": water, "outline_color": water, "outline_width": "0.1"}))
        styles.append(st)
    for name in ("water_lines",):
        st = QgsVectorTileBasicRendererStyle(name, name, QgsWkbTypes.LineGeometry)
        st.setSymbol(QgsLineSymbol.createSimple({"color": water, "width": "0.2"}))
        styles.append(st)
    # Piers and breakwaters stand on the water in the land colour
    for name in ("pier_polygons", "dam_polygons"):
        st = QgsVectorTileBasicRendererStyle(name, name, QgsWkbTypes.PolygonGeometry)
        st.setSymbol(QgsFillSymbol.createSimple({"color": land, "outline_style": "no"}))
        styles.append(st)
    for name in ("pier_lines", "dam_lines"):
        st = QgsVectorTileBasicRendererStyle(name, name, QgsWkbTypes.LineGeometry)
        st.setSymbol(QgsLineSymbol.createSimple({"color": land, "width": "0.4"}))
        styles.append(st)
    if buildings:
        st = QgsVectorTileBasicRendererStyle("buildings", "buildings",
                                             QgsWkbTypes.PolygonGeometry)
        st.setSymbol(QgsFillSymbol.createSimple(
            {"color": buildings, "outline_style": "no"}))
        styles.append(st)
    if streets:
        st = QgsVectorTileBasicRendererStyle("streets", "streets",
                                             QgsWkbTypes.LineGeometry)
        st.setSymbol(QgsLineSymbol.createSimple({"color": streets, "width": "0.12"}))
        styles.append(st)
    if border:
        st = QgsVectorTileBasicRendererStyle("boundaries", "boundaries",
                                             QgsWkbTypes.LineGeometry)
        st.setSymbol(QgsLineSymbol.createSimple(
            {"color": border, "width": "0.15", "line_style": "dash"}))
        styles.append(st)
    renderer = QgsVectorTileBasicRenderer()
    renderer.setStyles(styles)
    layer.setRenderer(renderer)
    return layer


def raster_layer(spec):
    layer = QgsRasterLayer(spec["path"], spec.get("name", "raster"))
    if not layer.isValid():
        raise RuntimeError("invalid raster " + spec["path"])
    ramp = QgsColorRampShader()
    ramp.setColorRampType(QgsColorRampShader.Interpolated)
    items = [QgsColorRampShader.ColorRampItem(v, QColor(c), str(v))
             for v, c in spec["ramp"]]
    ramp.setColorRampItemList(items)
    ramp.setClip(False)
    shader = QgsRasterShader()
    shader.setRasterShaderFunction(ramp)
    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
    layer.setRenderer(renderer)
    layer.renderer().setOpacity(spec.get("opacity", 1.0))
    return layer


def vector_layer(spec):
    uri = spec["path"] + ("|layername=" + spec["layer"] if "layer" in spec else "")
    layer = QgsVectorLayer(uri, spec.get("name", "vector"), "ogr")
    if not layer.isValid():
        raise RuntimeError("invalid vector " + uri)
    if "filter" in spec:
        layer.setSubsetString(spec["filter"])
    kind = spec.get("kind", "fill")
    if kind == "fill":
        sym = QgsFillSymbol.createSimple(spec["style"])
    elif kind == "line":
        sym = QgsLineSymbol.createSimple(spec["style"])
    else:
        sym = QgsMarkerSymbol.createSimple(spec["style"])
    if layer.renderer() is not None:
        layer.renderer().setSymbol(sym)
    if "label" in spec:
        lab = QgsPalLayerSettings()
        lab.fieldName = spec["label"]["field"]
        lab.isExpression = spec["label"].get("expression", False)
        fmt = QgsTextFormat()
        fmt.setFont(QFont(spec["label"].get("font", "Arial")))
        fmt.setSize(spec["label"].get("size", 8))
        fmt.setColor(QColor(spec["label"].get("color", "#000000")))
        buf = QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setSize(0.8)
        buf.setColor(QColor("#ffffff"))
        fmt.setBuffer(buf)
        lab.setFormat(fmt)
        if "placement" in spec["label"]:
            lab.placement = spec["label"]["placement"]
        layer.setLabeling(QgsVectorLayerSimpleLabeling(lab))
        layer.setLabelsEnabled(True)
    return layer


def draw_colorbar(painter, spec, width, height):
    x, y, w, h = spec["rect"]
    x *= width; y *= height; w *= width; h *= height
    grad = QLinearGradient(QPointF(x, y), QPointF(x + w, y))
    vmin = spec["ramp"][0][0]; vmax = spec["ramp"][-1][0]
    for v, c in spec["ramp"]:
        grad.setColorAt((v - vmin) / (vmax - vmin), QColor(c))
    painter.fillRect(QRectF(x, y, w, h), grad)
    painter.setPen(QPen(QColor("#333333"), 1))
    painter.drawRect(QRectF(x, y, w, h))
    font = QFont("Arial")
    font.setPixelSize(int(spec.get("font_px", 26)))
    painter.setFont(font)
    for v in spec["ticks"]:
        tx = x + (v - vmin) / (vmax - vmin) * w
        painter.drawLine(QPointF(tx, y + h), QPointF(tx, y + h + 8))
        text = str(v)
        adv = painter.fontMetrics().horizontalAdvance(text)
        painter.drawText(QPointF(tx - adv / 2, y + h + 8 + font.pixelSize()), text)
    title = spec.get("title", "")
    if title:
        painter.drawText(QPointF(x, y - 10), title)


def draw_legend(painter, spec, width, height):
    x, y = spec["origin"]
    x *= width; y *= height
    font = QFont("Arial")
    font.setPixelSize(int(spec.get("font_px", 26)))
    painter.setFont(font)
    box = font.pixelSize()
    step = int(box * 1.5)
    rows = spec["items"]
    title = spec.get("title", "")
    pad = box // 2
    text_w = max(painter.fontMetrics().horizontalAdvance(t) for t, _ in rows)
    title_w = painter.fontMetrics().horizontalAdvance(title) if title else 0
    top = y - (step if title else 0)
    frame_h = (step if title else 0) + step * len(rows)
    painter.fillRect(QRectF(x - pad, top - pad, max(box + pad + text_w, title_w) + 2 * pad,
                            frame_h + pad), QColor(255, 255, 255, 220))
    painter.setPen(QPen(QColor("#333333"), 1))
    if title:
        painter.drawText(QPointF(x, top + box), title)
    for i, (label, color) in enumerate(rows):
        ry = y + i * step
        painter.fillRect(QRectF(x, ry, box, box), QColor(color))
        painter.drawRect(QRectF(x, ry, box, box))
        painter.drawText(QPointF(x + box + pad, ry + box - 3), label)


def main(job_path):
    job = json.load(open(job_path, encoding="utf-8"))
    qgs = QgsApplication([], False)
    qgs.initQgis()
    crs = QgsCoordinateReferenceSystem(job["crs"])
    layers = []
    for spec in job["layers"]:
        if spec["type"] == "shortbread":
            layers.append(shortbread_layer(spec.get("water", "#c6dcef"),
                                           spec.get("border"),
                                           job.get("land", "#f2efe9"),
                                           spec.get("streets"),
                                           spec.get("buildings")))
        elif spec["type"] == "raster":
            layers.append(raster_layer(spec))
        else:
            layers.append(vector_layer(spec))
    for layer in layers:
        QgsProject.instance().addMapLayer(layer, False)
    settings = QgsMapSettings()
    settings.setDestinationCrs(crs)
    settings.setTransformContext(QgsProject.instance().transformContext())
    settings.setLayers(list(reversed(layers)))
    xmin, ymin, xmax, ymax = job["extent"]
    extent = QgsRectangle(xmin, ymin, xmax, ymax)
    if job.get("extent_crs") and job["extent_crs"] != job["crs"]:
        tr = QgsCoordinateTransform(QgsCoordinateReferenceSystem(job["extent_crs"]),
                                    crs, QgsProject.instance())
        extent = tr.transformBoundingBox(extent)
    settings.setExtent(extent)
    width, height = job["size"]
    settings.setOutputSize(QSize(width, height))
    settings.setOutputDpi(job.get("dpi", 300))
    settings.setBackgroundColor(QColor(job.get("land", "#f2efe9")))
    render = QgsMapRendererParallelJob(settings)
    render.start()
    render.waitForFinished()
    img = render.renderedImage()
    if "colorbar" in job or "legend" in job:
        out = QImage(img)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.Antialiasing)
        if "colorbar" in job:
            draw_colorbar(painter, job["colorbar"], width, height)
        if "legend" in job:
            draw_legend(painter, job["legend"], width, height)
        painter.end()
        img = out
    img.save(job["output"], "PNG")
    print("wrote", job["output"], width, height)
    qgs.exitQgis()


if __name__ == "__main__":
    main(sys.argv[1])
