import unittest
import xml.etree.ElementTree as ET
import base64
from io import BytesIO

from PIL import Image as PILImage

from core.ppt_generator.utils.svg_to_pptx.drawingml_context import ConvertContext
from core.ppt_generator.utils.svg_to_pptx.drawingml_elements import convert_image, convert_rect
from core.ppt_generator.utils.svg_to_pptx.drawingml_converter import convert_element, convert_g
from core.ppt_generator.utils.svg_to_pptx.drawingml_utils import px_to_emu


def _rect(**attrs):
    attributes = " ".join(f'{key}="{value}"' for key, value in attrs.items())
    return ET.fromstring(
        f'<rect xmlns="http://www.w3.org/2000/svg" x="0" y="0" '
        f'width="100" height="40" fill="#FFFFFF" {attributes}/>'
    )


class SVGToPPTXRectGeometryTests(unittest.TestCase):
    def test_plain_rect_stays_plain(self):
        result = convert_rect(_rect(), ConvertContext())
        self.assertIn('prst="rect"', result.xml)
        self.assertNotIn('prst="roundRect"', result.xml)

    def test_rx_only_uses_native_round_rect_with_matching_adjustment(self):
        result = convert_rect(_rect(rx="8"), ConvertContext())
        self.assertIn('prst="roundRect"', result.xml)
        self.assertIn('fmla="val 20000"', result.xml)

    def test_ry_only_follows_svg_missing_radius_semantics(self):
        result = convert_rect(_rect(ry="8"), ConvertContext())
        self.assertIn('prst="roundRect"', result.xml)
        self.assertIn('fmla="val 20000"', result.xml)

    def test_radius_is_clamped_to_pill_limit(self):
        result = convert_rect(_rect(rx="20", ry="20"), ConvertContext())
        self.assertIn('prst="roundRect"', result.xml)
        self.assertIn('fmla="val 50000"', result.xml)

    def test_unequal_radii_use_exact_custom_geometry(self):
        result = convert_rect(_rect(rx="12", ry="6"), ConvertContext())
        self.assertIn('<a:custGeom>', result.xml)
        self.assertIn('<a:cubicBezTo>', result.xml)

    def test_multi_pair_dash_array_is_not_truncated(self):
        elem = ET.fromstring(
            '<rect xmlns="http://www.w3.org/2000/svg" x="0" y="0" '
            'width="100" height="40" fill="none" stroke="#000000" '
            'stroke-width="2" stroke-dasharray="9 4 2 4"/>'
        )
        result = convert_rect(elem, ConvertContext())
        self.assertIn('<a:custDash>', result.xml)
        self.assertIn('<a:ds d="450000" sp="200000"/>', result.xml)
        self.assertIn('<a:ds d="100000" sp="200000"/>', result.xml)
        self.assertEqual(result.xml.count('<a:ds '), 2)


def _png_data_uri(width=4, height=2):
    output = BytesIO()
    PILImage.new('RGB', (width, height), '#336699').save(output, format='PNG')
    encoded = base64.b64encode(output.getvalue()).decode('ascii')
    return f'data:image/png;base64,{encoded}'


def _image(preserve_aspect_ratio=None):
    attrs = ''
    if preserve_aspect_ratio is not None:
        attrs = f' preserveAspectRatio="{preserve_aspect_ratio}"'
    return ET.fromstring(
        f'<image xmlns="http://www.w3.org/2000/svg" x="0" y="0" '
        f'width="100" height="100" href="{_png_data_uri()}"{attrs}/>'
    )


class SVGToPPTXImageGeometryTests(unittest.TestCase):
    def test_none_stretches_to_the_full_viewport(self):
        result = convert_image(_image('none'), ConvertContext())
        self.assertEqual(result.bounds_emu, (0, 0, 952500, 952500))
        self.assertNotIn('<a:srcRect', result.xml)

    def test_default_meet_contains_and_centers_the_image(self):
        result = convert_image(_image(), ConvertContext())
        self.assertEqual(result.bounds_emu, (0, 238125, 952500, 714375))
        self.assertNotIn('<a:srcRect', result.xml)

    def test_slice_crops_the_image_without_stretching(self):
        result = convert_image(_image('xMidYMid slice'), ConvertContext())
        self.assertEqual(result.bounds_emu, (0, 0, 952500, 952500))
        self.assertIn('<a:srcRect l="25000" t="0" r="25000" b="0"/>', result.xml)

    def test_slice_honors_minimum_alignment(self):
        result = convert_image(_image('xMinYMin slice'), ConvertContext())
        self.assertIn('<a:srcRect l="0" t="0" r="50000" b="0"/>', result.xml)

    def test_image_opacity_is_written_as_native_blip_alpha(self):
        image = _image('none')
        image.set('opacity', '0.4')
        context = ConvertContext(inherited_styles={'opacity': '0.5'})
        result = convert_image(image, context)
        self.assertIn('<a:alphaModFix amt="20000"/>', result.xml)

    def test_custom_image_clip_path_becomes_drawingml_custom_geometry(self):
        clip = ET.fromstring(
            '<clipPath xmlns="http://www.w3.org/2000/svg" id="ring">'
            '<path d="M 50 0 C 77.6 0 100 22.4 100 50 '
            'C 100 77.6 77.6 100 50 100 C 22.4 100 0 77.6 0 50 '
            'C 0 22.4 22.4 0 50 0 Z"/></clipPath>'
        )
        image = _image('none')
        image.set('clip-path', 'url(#ring)')
        result = convert_image(image, ConvertContext(defs={'ring': clip}))
        self.assertIn('<a:custGeom>', result.xml)
        self.assertIn('<a:cubicBezTo>', result.xml)


class SVGToPPTXTransformGeometryTests(unittest.TestCase):
    def test_nested_child_translation_inherits_parent_scale(self):
        parent = ConvertContext(scale_x=2, scale_y=3, translate_x=5, translate_y=7)
        child = parent.child(dx=10, dy=20)
        self.assertEqual((child.translate_x, child.translate_y), (25, 67))

    def test_group_rotation_uses_explicit_svg_pivot(self):
        group = ET.fromstring(
            '<g xmlns="http://www.w3.org/2000/svg" transform="rotate(90 0 0)">'
            '<rect x="100" y="20" width="40" height="20" fill="#000000"/>'
            '</g>'
        )
        result = convert_g(group, ConvertContext())
        self.assertIn('rot="5400000"', result.xml)
        self.assertIn('<a:off x="-476250" y="1047750"/>', result.xml)
        self.assertIn('<a:ext cx="381000" cy="190500"/>', result.xml)
        self.assertIn('<a:chOff x="952500" y="190500"/>', result.xml)
        self.assertEqual(result.bounds_emu, (-381000, 952500, -190500, 1333500))

    def test_element_rotation_is_routed_through_the_same_pivot_logic(self):
        rect = ET.fromstring(
            '<rect xmlns="http://www.w3.org/2000/svg" x="100" y="20" '
            'width="40" height="20" fill="#000000" transform="rotate(90 0 0)"/>'
        )
        result = convert_element(rect, ConvertContext())
        self.assertIn('<p:grpSp>', result.xml)
        self.assertIn('rot="5400000"', result.xml)

    def test_negative_scale_keeps_rect_geometry_instead_of_dropping_it(self):
        result = convert_rect(_rect(rx="8"), ConvertContext(translate_x=100, scale_x=-1))
        self.assertIsNotNone(result)
        self.assertEqual(result.bounds_emu, (0, 0, 952500, 381000))

    def test_nested_rotation_inside_nonuniform_parent_scale_keeps_visual_bounds(self):
        group = ET.fromstring(
            '<g xmlns="http://www.w3.org/2000/svg" '
            'transform="matrix(2 0 0 3 10 20)">'
            '<g transform="rotate(90 50 50)">'
            '<rect x="0" y="40" width="100" height="20" fill="#58C1DD"/>'
            '</g></g>'
        )
        result = convert_g(group, ConvertContext())
        self.assertEqual(
            result.bounds_emu,
            (px_to_emu(90), px_to_emu(20), px_to_emu(130), px_to_emu(320)),
        )
        self.assertIn('rot="5400000"', result.xml)


if __name__ == "__main__":
    unittest.main()
