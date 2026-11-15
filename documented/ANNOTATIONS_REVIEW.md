# Type annotation gaps

This report lists annotations left out because the type could not be determined confidently from the signature and local syntax. These are flagged for review rather than filled with `Any` or guessed types. Line numbers refer to files in `documented/`.

## `argparse2typer.py` — 22 untyped parameters; 6 untyped returns

- Line 106, `literal` parameters: `node`
- Line 106, `literal` return type: not inferred safely
- Line 115, `dotted` parameters: `node`
- Line 126, `is_parser_ctor` parameters: `node`
- Line 131, `is_parse_call` parameters: `node`
- Line 142, `is_main_guard` parameters: `node`
- Line 178, `walk_scope` parameters: `node`
- Line 178, `walk_scope` return type: not inferred safely
- Line 188, `identifiers_in` parameters: `stmts`
- Line 359, `convert_type` parameters: `node`
- Line 391, `help_expr` parameters: `node`, `default_val`
- Line 588, `str_literal` parameters: `node`
- Line 786, `plan_inline` parameters: `tree`
- Line 888, `apply_inline` parameters: `tree`
- Line 979, `find_entry` return type: not inferred safely
- Line 1021, `apply_bridge` parameters: `tree`
- Line 1053, `check_leftovers` parameters: `tree`
- Line 225, `__init__` return type: not inferred safely
- Line 234, `start` parameters: `node`
- Line 237, `end` parameters: `node`
- Line 240, `seg` parameters: `node`
- Line 252, `delete_stmt` parameters: `node`
- Line 276, `__init__` return type: not inferred safely
- Line 285, `warn` parameters: `node`
- Line 422, `lit` parameters: `key`, `default`
- Line 422, `lit` return type: not inferred safely

## `autogp.py` — 1 untyped parameters; 3 untyped returns

- Line 45, `setup_logging` return type: not inferred safely
- Line 133, `write_lines_safe` return type: not inferred safely
- Line 142, `ensure_gitignore` return type: not inferred safely
- Line 352, `main` parameters: `argv`

## `bundle_here.py` — 0 untyped parameters; 1 untyped returns

- Line 8, `main` return type: not inferred safely

## `chafa.py` — 40 untyped parameters; 6 untyped returns

- Line 375, `_in_ranges` parameters: `ranges`
- Line 1426, `_apply_fill` parameters: `fidx`, `chars`, `fg`, `bg`, `fill_symbols`, `cfg`, `fg_palette`, `bg_palette`, `Stot`, `Atot`, `consider_inverted`, `mode`, `truecolor`, `solid_char`
- Line 1500, `_apply_fill_fg_only` parameters: `fidx`, `chars`, `fg`, `bg`, `fill_symbols`, `cfg`, `fg_palette`, `Stot`, `Atot`, `fg_def`, `bg_def`
- Line 1578, `_emit_attrs` parameters: `fg`, `bg`, `inverted`, `bold`
- Line 1578, `_emit_attrs` return type: not inferred safely
- Line 1611, `_unpack` parameters: `pen`
- Line 1616, `_pen_transparent` parameters: `pen`
- Line 1622, `_sgr_fg` parameters: `pen`
- Line 1633, `_sgr_bg` parameters: `pen`
- Line 1644, `_sgr_fgbg` parameters: `fg`, `bg`
- Line 1656, `_emit_attrs` parameters: `fg`, `bg`, `inverted`, `bold`
- Line 1779, `_scaled_placement` return type: not inferred safely
- Line 1934, `iter_frames` return type: not inferred safely
- Line 198, `__init__` return type: not inferred safely
- Line 411, `__init__` return type: not inferred safely
- Line 1208, `__init__` return type: not inferred safely

## `csshtmlpret.py` — 4 untyped parameters; 1 untyped returns

- Line 56, `_props_grouper` parameters: `props`, `pgs`
- Line 56, `_props_grouper` return type: not inferred safely
- Line 245, `process_multiple_files` parameters: `path`
- Line 205, `prettify` parameters: `encoding`

## `dupfx.py` — 8 untyped parameters; 4 untyped returns

- Line 46, `file_stat_key` parameters: `p`
- Line 46, `file_stat_key` return type: not inferred safely
- Line 54, `quick_hash` parameters: `path`, `n`
- Line 54, `quick_hash` return type: not inferred safely
- Line 74, `full_hash` parameters: `path`
- Line 93, `iter_files` parameters: `root`, `recursive`, `follow_symlinks`
- Line 93, `iter_files` return type: not inferred safely
- Line 114, `choose_keep` parameters: `files`
- Line 114, `choose_keep` return type: not inferred safely

## `env_audit.py` — 0 untyped parameters; 2 untyped returns

- Line 100, `__init__` return type: not inferred safely
- Line 135, `_handle_env_access` return type: not inferred safely

## `git_trim.py` — 49 untyped parameters; 1 untyped returns

- Line 815, `_analyze` return type: not inferred safely
- Line 1254, `_scan` parameters: `capsys`
- Line 1335, `test_plain_merge_classified_merged` parameters: `tmp_path`, `capsys`
- Line 1348, `test_single_commit_squash_detected` parameters: `tmp_path`, `capsys`
- Line 1361, `test_multicommit_squash_detected` parameters: `tmp_path`, `capsys`
- Line 1373, `test_rebase_merge_detected` parameters: `tmp_path`, `capsys`
- Line 1387, `test_unmerged_branch_is_unsafe_and_survives_delete` parameters: `tmp_path`, `capsys`
- Line 1402, `test_branch_in_worktree_is_skipped` parameters: `tmp_path`, `capsys`
- Line 1418, `test_current_branch_is_never_a_candidate` parameters: `tmp_path`, `capsys`
- Line 1432, `test_keep_patterns_and_defaults` parameters: `tmp_path`, `capsys`
- Line 1452, `test_dryrun_yes_undo_roundtrip` parameters: `tmp_path`, `capsys`
- Line 1484, `test_undo_restores_upstream_tracking` parameters: `tmp_path`, `capsys`
- Line 1512, `test_delete_pattern_filters` parameters: `tmp_path`, `capsys`
- Line 1526, `test_scan_exit_codes` parameters: `tmp_path`, `capsys`
- Line 1542, `test_scan_json_shape` parameters: `tmp_path`, `capsys`
- Line 1572, `test_gone_upstream_counts_as_no_upstream` parameters: `tmp_path`, `capsys`
- Line 1593, `test_prune_remote_deletes_merged_gone_and_undo_restores` parameters: `tmp_path`, `capsys`
- Line 1626, `test_stale_window_rules` parameters: `tmp_path`, `capsys`
- Line 1652, `test_shallow_clone_downgrades_to_unknown` parameters: `tmp_path`, `capsys`
- Line 1669, `test_orphan_upstream_warning` parameters: `tmp_path`, `capsys`
- Line 1683, `test_detached_head_scan_works` parameters: `tmp_path`, `capsys`
- Line 1696, `test_base_autodetect_master_and_origin_head` parameters: `tmp_path`, `capsys`
- Line 1713, `test_base_prefers_remote_when_strictly_ahead` parameters: `tmp_path`, `capsys`
- Line 1741, `test_undo_without_log` parameters: `tmp_path`, `capsys`
- Line 1750, `test_extra_unmerged_commit_keeps_branch_unmerged` parameters: `tmp_path`, `capsys`
- Line 1765, `test_demo_runs` parameters: `tmp_path`, `capsys`

## `hasdoc.py` — 5 untyped parameters; 3 untyped returns

- Line 37, `iter_py_files` return type: not inferred safely
- Line 69, `_is_docstring_stmt` parameters: `stmt`
- Line 73, `_parse` return type: not inferred safely
- Line 115, `main` parameters: `argv`
- Line 92, `first_stmt_docstring` parameters: `node`, `kind`, `name`
- Line 92, `first_stmt_docstring` return type: not inferred safely

## `infer.py` — 11 untyped parameters; 3 untyped returns

- Line 10, `infer_from_expr` parameters: `expr`
- Line 10, `infer_from_expr` return type: not inferred safely
- Line 67, `_last_is_exit` parameters: `body`
- Line 78, `infer_return` parameters: `func`
- Line 51, `visit_FunctionDef` parameters: `node`
- Line 54, `visit_ClassDef` parameters: `node`
- Line 57, `visit_Lambda` parameters: `node`
- Line 60, `visit_Yield` parameters: `node`
- Line 63, `visit_Return` parameters: `node`
- Line 124, `_annotate_params` parameters: `params`
- Line 124, `_annotate_params` return type: not inferred safely
- Line 141, `leave_FunctionDef` parameters: `original_node`, `updated_node`
- Line 141, `leave_FunctionDef` return type: not inferred safely

## `mhtml_to_html.py` — 44 untyped parameters; 7 untyped returns

- Line 2503, `_tag_name` parameters: `node`
- Line 2516, `_serialize_doctype` parameters: `doctype`
- Line 2528, `serialize_html_node` parameters: `node`
- Line 2537, `_serialize_text_node` parameters: `text_node`
- Line 2547, `_should_escape_raw_text` parameters: `parent`
- Line 2582, `_serialize_element` parameters: `element`
- Line 2595, `_serialize_children` parameters: `element`
- Line 2601, `_serialize_attributes` parameters: `element`
- Line 2618, `element_get_attribute` parameters: `element`
- Line 2631, `element_get_attribute_names` parameters: `element`
- Line 2636, `element_set_attribute` parameters: `element`
- Line 2640, `element_remove_attribute` parameters: `element`
- Line 2652, `element_append_child` parameters: `parent`, `child`
- Line 2657, `element_remove` parameters: `node`
- Line 2667, `element_replace_with` parameters: `node`, `new_nodes`
- Line 2681, `element_prepend` parameters: `parent`, `new_nodes`
- Line 2687, `element_after` parameters: `node`, `new_nodes`
- Line 2700, `get_meta_charset_elements` parameters: `document_element`
- Line 2711, `get_meta_content_type_elements` parameters: `document_element`
- Line 3343, `_resolve_reference` parameters: `missing_resources`
- Line 3365, `_set_attribute` parameters: `element`
- Line 3377, `_replace_stylesheet_urls` parameters: `missing_resources`
- Line 3539, `_convert_impl` return type: not inferred safely
- Line 4034, `_element_text_content` parameters: `element`
- Line 4109, `_read_capped` parameters: `response`
- Line 4238, `_fetch_resources` parameters: `mhtml_obj`
- Line 1630, `__init__` parameters: `value`
- Line 1640, `__init__` parameters: `block`
- Line 1650, `__init__` parameters: `block`
- Line 2448, `__init__` parameters: `document`
- Line 2455, `document_element` return type: not inferred safely
- Line 2462, `doctype` return type: not inferred safely
- Line 2469, `head` return type: not inferred safely
- Line 2482, `create_element` return type: not inferred safely
- Line 2486, `create_text_node` return type: not inferred safely
- Line 3401, `visit` parameters: `node`
- Line 4102, `redirect_request` parameters: `req`, `fp`, `code`, `msg`, `headers`, `newurl`
- Line 4102, `redirect_request` return type: not inferred safely
- Line 4280, `_with` parameters: `changes`
- Line 3797, `found_srcset` parameters: `_c`
- Line 3802, `not_found_srcset` parameters: `_u`, `_c`

## `mmdc.py` — 9 untyped parameters; 0 untyped returns

- Line 5079, `renderMermaid` parameters: `browser`, `definition`, `outputFormat`, `opt`
- Line 2672, `key` parameters: `_b`, `_g`, `_p`
- Line 2680, `key` parameters: `_b`, `_p`

## `monolith.py` — 17 untyped parameters; 7 untyped returns

- Line 418, `_handle_link` parameters: `attrs`, `sc`
- Line 418, `_handle_link` return type: not inferred safely
- Line 440, `_handle_script` parameters: `attrs`, `sc`
- Line 440, `_handle_script` return type: not inferred safely
- Line 457, `_handle_style` parameters: `attrs`, `sc`
- Line 457, `_handle_style` return type: not inferred safely
- Line 467, `_handle_media` parameters: `tag`, `attrs`, `sc`, `kind`, `url_attrs`
- Line 467, `_handle_media` return type: not inferred safely
- Line 488, `_handle_iframe` parameters: `attrs`, `sc`
- Line 488, `_handle_iframe` return type: not inferred safely
- Line 499, `_handle_anchor` parameters: `attrs`
- Line 499, `_handle_anchor` return type: not inferred safely
- Line 504, `_process_style_attr` parameters: `attrs`
- Line 504, `_process_style_attr` return type: not inferred safely
- Line 530, `_render_attrs` parameters: `attrs`
- Line 539, `_render` parameters: `attrs`

## `pipdown.py` — 0 untyped parameters; 3 untyped returns

- Line 203, `parse_html` return type: not inferred safely
- Line 221, `parse_json` return type: not inferred safely
- Line 100, `parsed` return type: not inferred safely

## `pyocr.py` — 151 untyped parameters; 119 untyped returns

- Line 60, `digits_only` parameters: `string`
- Line 707, `psm_parameter` return type: not inferred safely
- Line 718, `tess_can_detect_orientation` return type: not inferred safely
- Line 724, `tess_detect_orientation` parameters: `image`, `lang`
- Line 783, `tess_run_tesseract` parameters: `input_filename`, `output_filename_base`, `cwd`, `lang`, `flags`, `configs`
- Line 806, `tess_cleanup` parameters: `filename`
- Line 806, `tess_cleanup` return type: not inferred safely
- Line 832, `tess_image_to_string` parameters: `image`, `lang`, `builder`
- Line 832, `tess_image_to_string` return type: not inferred safely
- Line 866, `tess_is_available` return type: not inferred safely
- Line 888, `tess_get_version` return type: not inferred safely
- Line 1179, `raw_init` parameters: `lang`
- Line 1179, `raw_init` return type: not inferred safely
- Line 1198, `raw_cleanup` parameters: `handle`
- Line 1198, `raw_cleanup` return type: not inferred safely
- Line 1203, `raw_is_available` return type: not inferred safely
- Line 1207, `raw_get_version` return type: not inferred safely
- Line 1212, `raw_get_available_languages` parameters: `handle`
- Line 1212, `raw_get_available_languages` return type: not inferred safely
- Line 1223, `raw_set_is_numeric` parameters: `handle`, `mode`
- Line 1223, `raw_set_is_numeric` return type: not inferred safely
- Line 1229, `raw_set_debug_file` parameters: `handle`, `filename`
- Line 1229, `raw_set_debug_file` return type: not inferred safely
- Line 1236, `raw_set_page_seg_mode` parameters: `handle`, `mode`
- Line 1236, `raw_set_page_seg_mode` return type: not inferred safely
- Line 1241, `raw_init_for_analyse_page` parameters: `handle`
- Line 1241, `raw_init_for_analyse_page` return type: not inferred safely
- Line 1246, `raw_set_image` parameters: `handle`, `image`
- Line 1246, `raw_set_image` return type: not inferred safely
- Line 1263, `raw_recognize` parameters: `handle`
- Line 1263, `raw_recognize` return type: not inferred safely
- Line 1268, `raw_analyse_layout` parameters: `handle`
- Line 1268, `raw_analyse_layout` return type: not inferred safely
- Line 1273, `raw_get_utf8_text` parameters: `handle`
- Line 1273, `raw_get_utf8_text` return type: not inferred safely
- Line 1281, `raw_page_iterator_delete` parameters: `iterator`
- Line 1281, `raw_page_iterator_delete` return type: not inferred safely
- Line 1286, `raw_page_iterator_next` parameters: `iterator`, `level`
- Line 1286, `raw_page_iterator_next` return type: not inferred safely
- Line 1291, `raw_page_iterator_is_at_beginning_of` parameters: `iterator`, `level`
- Line 1291, `raw_page_iterator_is_at_beginning_of` return type: not inferred safely
- Line 1296, `raw_page_iterator_is_at_final_element` parameters: `iterator`, `level`, `element`
- Line 1296, `raw_page_iterator_is_at_final_element` return type: not inferred safely
- Line 1301, `raw_page_iterator_block_type` parameters: `iterator`
- Line 1301, `raw_page_iterator_block_type` return type: not inferred safely
- Line 1306, `raw_page_iterator_bounding_box` parameters: `iterator`, `level`
- Line 1325, `raw_page_iterator_orientation` parameters: `iterator`
- Line 1346, `raw_get_iterator` parameters: `handle`
- Line 1346, `raw_get_iterator` return type: not inferred safely
- Line 1351, `raw_result_iterator_get_page_iterator` parameters: `res_iterator`
- Line 1351, `raw_result_iterator_get_page_iterator` return type: not inferred safely
- Line 1356, `raw_result_iterator_get_utf8_text` parameters: `iterator`, `level`
- Line 1356, `raw_result_iterator_get_utf8_text` return type: not inferred safely
- Line 1366, `raw_result_iterator_get_confidence` parameters: `iterator`, `level`
- Line 1366, `raw_result_iterator_get_confidence` return type: not inferred safely
- Line 1374, `raw_detect_os` parameters: `handle`
- Line 1408, `raw_set_input_name` parameters: `handle`, `input_file`
- Line 1408, `raw_set_input_name` return type: not inferred safely
- Line 1413, `raw_init_pdf_renderer` parameters: `handle`, `output_file`, `textonly`
- Line 1413, `raw_init_pdf_renderer` return type: not inferred safely
- Line 1419, `raw_begin_document` parameters: `renderer`, `doc_name`
- Line 1419, `raw_begin_document` return type: not inferred safely
- Line 1424, `raw_add_renderer_image` parameters: `handle`, `renderer`
- Line 1424, `raw_add_renderer_image` return type: not inferred safely
- Line 1429, `raw_end_document` parameters: `renderer`
- Line 1429, `raw_end_document` return type: not inferred safely
- Line 1434, `libtess_can_detect_orientation` return type: not inferred safely
- Line 1438, `libtess_detect_orientation` parameters: `image`, `lang`
- Line 1472, `_tess_box_to_pyocr_box` parameters: `box`
- Line 1476, `libtess_image_to_string` parameters: `image`, `lang`, `builder`
- Line 1476, `libtess_image_to_string` return type: not inferred safely
- Line 1520, `libtess_image_to_pdf` parameters: `image`, `output_file`, `lang`
- Line 1520, `libtess_image_to_pdf` return type: not inferred safely
- Line 1592, `libtess_get_available_languages` return type: not inferred safely
- Line 1633, `_cun_temp_file` parameters: `suffix`
- Line 1633, `_cun_temp_file` return type: not inferred safely
- Line 1637, `cun_image_to_string` parameters: `image`, `lang`, `builder`
- Line 1637, `cun_image_to_string` return type: not inferred safely
- Line 1665, `cun_is_available` return type: not inferred safely
- Line 1669, `cun_get_available_languages` return type: not inferred safely
- Line 1685, `cun_get_version` return type: not inferred safely
- Line 1753, `_load_image` parameters: `path`
- Line 1753, `_load_image` return type: not inferred safely
- Line 1762, `_print_result` parameters: `result`
- Line 1772, `_cmd_tools` parameters: `args`
- Line 1772, `_cmd_tools` return type: not inferred safely
- Line 1784, `_cmd_langs` parameters: `args`
- Line 1784, `_cmd_langs` return type: not inferred safely
- Line 1793, `_cmd_orientation` parameters: `args`
- Line 1793, `_cmd_orientation` return type: not inferred safely
- Line 1807, `_cmd_ocr` parameters: `args`
- Line 1807, `_cmd_ocr` return type: not inferred safely
- Line 1818, `main` parameters: `argv`
- Line 43, `__init__` parameters: `status`, `message`
- Line 43, `__init__` return type: not inferred safely
- Line 52, `__init__` parameters: `status`, `message`
- Line 52, `__init__` return type: not inferred safely
- Line 68, `__init__` parameters: `content`, `position`
- Line 68, `__init__` return type: not inferred safely
- Line 74, `get_xml_tag` parameters: `parent_doc`
- Line 74, `get_xml_tag` return type: not inferred safely
- Line 94, `__str__` return type: not inferred safely
- Line 104, `__box_cmp` parameters: `other`
- Line 104, `__box_cmp` return type: not inferred safely
- Line 119, `__lt__` parameters: `other`
- Line 119, `__lt__` return type: not inferred safely
- Line 122, `__gt__` parameters: `other`
- Line 122, `__gt__` return type: not inferred safely
- Line 125, `__eq__` parameters: `other`
- Line 125, `__eq__` return type: not inferred safely
- Line 128, `__le__` parameters: `other`
- Line 128, `__le__` return type: not inferred safely
- Line 131, `__ge__` parameters: `other`
- Line 131, `__ge__` return type: not inferred safely
- Line 134, `__ne__` parameters: `other`
- Line 134, `__ne__` return type: not inferred safely
- Line 137, `__hash__` return type: not inferred safely
- Line 147, `__init__` parameters: `word_boxes`, `position`
- Line 147, `__init__` return type: not inferred safely
- Line 153, `content` return type: not inferred safely
- Line 159, `get_xml_tag` parameters: `parent_doc`
- Line 159, `get_xml_tag` return type: not inferred safely
- Line 181, `__str__` return type: not inferred safely
- Line 200, `__repr__` return type: not inferred safely
- Line 204, `__contains__` parameters: `text`
- Line 204, `__contains__` return type: not inferred safely
- Line 207, `__box_cmp` parameters: `other`
- Line 207, `__box_cmp` return type: not inferred safely
- Line 222, `__lt__` parameters: `other`
- Line 222, `__lt__` return type: not inferred safely
- Line 225, `__gt__` parameters: `other`
- Line 225, `__gt__` return type: not inferred safely
- Line 228, `__eq__` parameters: `other`
- Line 228, `__eq__` return type: not inferred safely
- Line 231, `__le__` parameters: `other`
- Line 231, `__le__` return type: not inferred safely
- Line 234, `__ge__` parameters: `other`
- Line 234, `__ge__` return type: not inferred safely
- Line 237, `__ne__` parameters: `other`
- Line 237, `__ne__` return type: not inferred safely
- Line 240, `__hash__` return type: not inferred safely
- Line 251, `__init__` parameters: `file_extensions`, `tesseract_flags`, `tesseract_configs`, `cuneiform_args`
- Line 251, `__init__` return type: not inferred safely
- Line 258, `read_file` parameters: `file_descriptor`
- Line 258, `read_file` return type: not inferred safely
- Line 263, `write_file` parameters: `file_descriptor`, `output`
- Line 263, `write_file` return type: not inferred safely
- Line 268, `start_line` parameters: `box`
- Line 268, `start_line` return type: not inferred safely
- Line 272, `add_word` parameters: `word`, `box`
- Line 272, `add_word` return type: not inferred safely
- Line 277, `end_line` return type: not inferred safely
- Line 281, `get_output` return type: not inferred safely
- Line 288, `__init__` return type: not inferred safely
- Line 311, `read_file` parameters: `file_descriptor`
- Line 311, `read_file` return type: not inferred safely
- Line 316, `write_file` parameters: `file_descriptor`, `text`
- Line 316, `write_file` return type: not inferred safely
- Line 320, `start_line` parameters: `box`
- Line 320, `start_line` return type: not inferred safely
- Line 323, `add_word` parameters: `word`, `box`
- Line 323, `add_word` return type: not inferred safely
- Line 329, `end_line` return type: not inferred safely
- Line 332, `get_output` return type: not inferred safely
- Line 342, `__init__` return type: not inferred safely
- Line 356, `__init__` return type: not inferred safely
- Line 369, `__parse_confidence` parameters: `title`
- Line 379, `__parse_position` parameters: `title`
- Line 388, `handle_starttag` parameters: `tag`, `attrs`
- Line 416, `handle_data` parameters: `data`
- Line 422, `handle_endtag` parameters: `tag`
- Line 454, `__init__` return type: not inferred safely
- Line 461, `handle_starttag` parameters: `tag`, `attrs`
- Line 489, `handle_data` parameters: `data`
- Line 495, `handle_endtag` parameters: `tag`
- Line 518, `__init__` return type: not inferred safely
- Line 525, `read_file` parameters: `file_descriptor`
- Line 525, `read_file` return type: not inferred safely
- Line 538, `write_file` parameters: `file_descriptor`, `boxes`
- Line 538, `write_file` return type: not inferred safely
- Line 548, `start_line` parameters: `box`
- Line 548, `start_line` return type: not inferred safely
- Line 551, `add_word` parameters: `word`, `box`
- Line 551, `add_word` return type: not inferred safely
- Line 555, `end_line` return type: not inferred safely
- Line 558, `get_output` return type: not inferred safely
- Line 568, `__init__` return type: not inferred safely
- Line 575, `read_file` parameters: `file_descriptor`
- Line 575, `read_file` return type: not inferred safely
- Line 594, `write_file` parameters: `file_descriptor`, `boxes`
- Line 594, `write_file` return type: not inferred safely
- Line 604, `start_line` parameters: `box`
- Line 609, `add_word` parameters: `word`, `box`
- Line 609, `add_word` return type: not inferred safely
- Line 613, `end_line` return type: not inferred safely
- Line 616, `get_output` return type: not inferred safely
- Line 626, `__init__` return type: not inferred safely
- Line 637, `__init__` return type: not inferred safely
- Line 643, `read_file` parameters: `file_descriptor`
- Line 643, `read_file` return type: not inferred safely
- Line 661, `write_file` parameters: `file_descriptor`, `boxes`
- Line 661, `write_file` return type: not inferred safely
- Line 812, `__init__` parameters: `suffix`
- Line 812, `__init__` return type: not inferred safely
- Line 818, `__enter__` return type: not inferred safely
- Line 822, `__exit__` parameters: `type`, `value`, `traceback`
- Line 822, `__exit__` return type: not inferred safely
- Line 826, `close` return type: not inferred safely
- Line 1527, `__init__` return type: not inferred safely
- Line 1534, `set_lang` parameters: `lang`
- Line 1534, `set_lang` return type: not inferred safely
- Line 1539, `set_output_file` parameters: `output_file`
- Line 1539, `set_output_file` return type: not inferred safely
- Line 1544, `set_text_only` parameters: `text_only`
- Line 1544, `set_text_only` return type: not inferred safely
- Line 1549, `add_image` parameters: `img`
- Line 1549, `add_image` return type: not inferred safely
- Line 1554, `__validate` return type: not inferred safely
- Line 1562, `build` return type: not inferred safely

## `reformat_python.py` — 0 untyped parameters; 1 untyped returns

- Line 1025, `__init__` return type: not inferred safely

## `scraper.py` — 7 untyped parameters; 5 untyped returns

- Line 427, `_yt_recent_videos` parameters: `yt`
- Line 734, `_sigint` parameters: `_sig`, `_frame`
- Line 734, `_sigint` return type: not inferred safely
- Line 894, `_check_image_size` return type: not inferred safely
- Line 911, `_download_image` parameters: `item`
- Line 250, `_iter_py` return type: not inferred safely
- Line 807, `save_state` parameters: `q`, `v`
- Line 807, `save_state` return type: not inferred safely
- Line 811, `append` parameters: `matches`
- Line 811, `append` return type: not inferred safely

## `shorten_names.py` — 12 untyped parameters; 0 untyped returns

- Line 273, `visit_FunctionDef` parameters: `node`
- Line 277, `leave_FunctionDef` parameters: `original_node`
- Line 280, `visit_ClassDef` parameters: `node`
- Line 284, `leave_ClassDef` parameters: `original_node`
- Line 287, `visit_Import` parameters: `node`
- Line 291, `leave_Import` parameters: `original_node`
- Line 294, `visit_ImportFrom` parameters: `node`
- Line 298, `leave_ImportFrom` parameters: `original_node`
- Line 551, `visit_FunctionDef` parameters: `node`
- Line 555, `visit_ClassDef` parameters: `node`
- Line 559, `visit_TypeAlias` parameters: `node`
- Line 568, `_collect_type_parameters` parameters: `type_parameters`

## `shortener.py` — 1 untyped parameters; 1 untyped returns

- Line 9, `process_file` parameters: `path`
- Line 9, `process_file` return type: not inferred safely

## `similarity_check.py` — 5 untyped parameters; 1 untyped returns

- Line 1073, `frame` parameters: `x`, `y`, `w`, `h`, `title`
- Line 1073, `frame` return type: not inferred safely

## `sonic.py` — 7 untyped parameters; 0 untyped returns

- Line 489, `coalesce` parameters: `group_key`
- Line 369, `sort_in_memory` parameters: `key`
- Line 374, `_write_chunk` parameters: `key`
- Line 383, `create_sorted_chunks` parameters: `key`
- Line 434, `merge_sorted_files` parameters: `key`
- Line 515, `__call__` parameters: `group_key`
- Line 525, `unsorted` parameters: `group_key`

## `sr.py` — 0 untyped parameters; 2 untyped returns

- Line 269, `iter_files` return type: not inferred safely
- Line 285, `walk_dir` return type: not inferred safely

## `tcleaner.py` — 3 untyped parameters; 3 untyped returns

- Line 47, `expand_and_resolve` parameters: `pattern`
- Line 47, `expand_and_resolve` return type: not inferred safely
- Line 51, `is_exempt` parameters: `path`
- Line 63, `get_size` parameters: `path`
- Line 63, `get_size` return type: not inferred safely
- Line 82, `purge_system` return type: not inferred safely

## `tcs.py` — 3 untyped parameters; 3 untyped returns

- Line 352, `strip_suite` parameters: `suite`
- Line 352, `strip_suite` return type: not inferred safely
- Line 174, `_strip_body` parameters: `node`
- Line 174, `_strip_body` return type: not inferred safely
- Line 198, `_strip_funcdef` parameters: `node`
- Line 198, `_strip_funcdef` return type: not inferred safely

## `termux_cleaner.py` — 1 untyped parameters; 2 untyped returns

- Line 43, `expand_and_resolve` parameters: `pattern`
- Line 43, `expand_and_resolve` return type: not inferred safely
- Line 48, `purge_system` return type: not inferred safely

## `vsvg.py` — 218 untyped parameters; 53 untyped returns

- Line 73, `_f` parameters: `value`
- Line 73, `_f` return type: not inferred safely
- Line 121, `_hsl_to_rgb` parameters: `h`, `s`, `l`
- Line 143, `parse_color` parameters: `value`
- Line 143, `parse_color` return type: not inferred safely
- Line 225, `parse_length` parameters: `value`, `pct_base`, `default`
- Line 225, `parse_length` return type: not inferred safely
- Line 283, `parse_transform` parameters: `text`
- Line 283, `parse_transform` return type: not inferred safely
- Line 383, `_cubic` parameters: `p0`, `p1`, `p2`, `p3`, `tol`, `out`
- Line 417, `_quad` parameters: `p0`, `p1`, `p2`, `tol`, `out`
- Line 442, `_arc` parameters: `x0`, `y0`, `rx`, `ry`, `phi_deg`, `laf`, `sf`, `x1`, `y1`, `tol`
- Line 442, `_arc` return type: not inferred safely
- Line 507, `flatten_path` parameters: `d`, `tol`
- Line 507, `flatten_path` return type: not inferred safely
- Line 688, `_seg_count` parameters: `radius`, `tol`
- Line 688, `_seg_count` return type: not inferred safely
- Line 696, `_ellipse_pts` parameters: `cx`, `cy`, `rx`, `ry`, `tol`
- Line 696, `_ellipse_pts` return type: not inferred safely
- Line 705, `_rounded_rect_pts` parameters: `x`, `y`, `w`, `h`, `rx`, `ry`, `tol`
- Line 705, `_rounded_rect_pts` return type: not inferred safely
- Line 728, `shape_subpaths` parameters: `tag`, `el`, `tol`
- Line 728, `shape_subpaths` return type: not inferred safely
- Line 789, `_disc` parameters: `cx`, `cy`, `r`
- Line 793, `_dashes` parameters: `pts`, `closed`, `pattern`, `offset`
- Line 793, `_dashes` return type: not inferred safely
- Line 856, `_stroke_run` parameters: `pts`, `closed`, `hw`, `cap`, `join`, `miter`
- Line 856, `_stroke_run` return type: not inferred safely
- Line 938, `stroke_polygons` parameters: `subpaths`, `hw`, `cap`, `join`, `miter`, `dash`, `dashoff`
- Line 938, `stroke_polygons` return type: not inferred safely
- Line 1019, `_local` parameters: `tag`
- Line 1019, `_local` return type: not inferred safely
- Line 1025, `_style` parameters: `el`, `inherited`
- Line 1025, `_style` return type: not inferred safely
- Line 1071, `_parse_view_box` parameters: `v`
- Line 1071, `_parse_view_box` return type: not inferred safely
- Line 1080, `root_matrix` parameters: `root`, `pw`, `ph`
- Line 1080, `root_matrix` return type: not inferred safely
- Line 1119, `intrinsic_size` parameters: `root`
- Line 1290, `_bbox` parameters: `polys`
- Line 1306, `_edges_of` parameters: `polys`, `closed_ring`
- Line 1306, `_edges_of` return type: not inferred safely
- Line 1328, `_sweep` parameters: `W`, `H`, `edges`, `y_lo`, `y_hi`, `on_span`
- Line 1367, `_fill_evenodd` parameters: `buf`, `W`, `H`, `polys`, `cr`, `cg`, `cb`, `ca`
- Line 1408, `_fill_nonzero` parameters: `buf`, `W`, `H`, `polys`, `cr`, `cg`, `cb`, `ca`
- Line 1481, `_mask_polys` parameters: `mask`, `W`, `H`, `polys`
- Line 1504, `_blend_mask` parameters: `buf`, `W`, `H`, `mask`, `cr`, `cg`, `cb`, `ca`
- Line 1504, `_blend_mask` return type: not inferred safely
- Line 1526, `_downsample` parameters: `buf`, `W`, `H`, `ss`, `pw`, `ph`
- Line 1556, `rasterize` parameters: `items`, `pw`, `ph`
- Line 1591, `encode_png` parameters: `w`, `h`, `rgba`
- Line 1591, `encode_png` return type: not inferred safely
- Line 1610, `_paeth` parameters: `a`, `b`, `c`
- Line 1610, `_paeth` return type: not inferred safely
- Line 1621, `decode_png` parameters: `data`
- Line 1820, `composite_rgb` parameters: `w`, `h`, `rgba`, `bg`
- Line 1820, `composite_rgb` return type: not inferred safely
- Line 1852, `_to256` parameters: `c`
- Line 1852, `_to256` return type: not inferred safely
- Line 1869, `render_blocks` parameters: `w`, `h`, `rgb`, `bg`, `truecolor`
- Line 1869, `render_blocks` return type: not inferred safely
- Line 1910, `render_sixel` parameters: `w`, `h`, `rgb`
- Line 1979, `render_kitty` parameters: `w`, `h`, `rgba`, `cols`, `rows`
- Line 1979, `render_kitty` return type: not inferred safely
- Line 2005, `paste_center` parameters: `img`, `pw`, `ph`
- Line 2026, `load_cairosvg` return type: not inferred safely
- Line 2036, `cairosvg_render` parameters: `cs`, `data`, `pw`, `ph`, `intrinsic`
- Line 2036, `cairosvg_render` return type: not inferred safely
- Line 2051, `cairosvg_png` parameters: `cs`, `data`, `width`
- Line 2051, `cairosvg_png` return type: not inferred safely
- Line 2073, `_auto_ss` parameters: `pw`, `ph`, `ss`
- Line 2073, `_auto_ss` return type: not inferred safely
- Line 2081, `render_file` parameters: `args`, `term`, `renderer`, `data`, `name`, `status`
- Line 2128, `emit` parameters: `renderer`, `img`, `term`, `bg`, `truecolor`
- Line 2128, `emit` return type: not inferred safely
- Line 2138, `export_png` parameters: `args`, `status`, `data`, `name`
- Line 2138, `export_png` return type: not inferred safely
- Line 2168, `main` parameters: `argv`
- Line 2168, `main` return type: not inferred safely
- Line 130, `cu` parameters: `t`
- Line 130, `cu` return type: not inferred safely
- Line 254, `__init__` return type: not inferred safely
- Line 258, `__matmul__` parameters: `o`
- Line 258, `__matmul__` return type: not inferred safely
- Line 269, `apply` parameters: `x`, `y`
- Line 272, `scale` return type: not inferred safely
- Line 332, `__init__` parameters: `d`
- Line 332, `__init__` return type: not inferred safely
- Line 338, `_ws` return type: not inferred safely
- Line 345, `cmd` return type: not inferred safely
- Line 476, `angle` parameters: `ux`, `uy`, `vx`, `vy`
- Line 476, `angle` return type: not inferred safely
- Line 518, `newsub` parameters: `px`, `py`
- Line 518, `newsub` return type: not inferred safely
- Line 526, `ensure` return type: not inferred safely
- Line 714, `cubic` parameters: `x0`, `y0`, `x1`, `y1`, `x2`, `y2`, `x3`, `y3`
- Line 714, `cubic` return type: not inferred safely
- Line 1050, `__init__` return type: not inferred safely
- Line 1055, `add` parameters: `what`
- Line 1055, `add` return type: not inferred safely
- Line 1059, `empty` return type: not inferred safely
- Line 1062, `text` return type: not inferred safely
- Line 1134, `__init__` parameters: `data`
- Line 1134, `__init__` return type: not inferred safely
- Line 1150, `build` parameters: `pw`, `ph`
- Line 1159, `_walk` parameters: `el`, `mat`, `st`, `items`, `warn`, `use_stack`
- Line 1376, `span` parameters: `x0`, `x1`, `y`
- Line 1487, `span` parameters: `x0`, `x1`, `y`
- Line 1594, `chunk` parameters: `tag`, `data`
- Line 1594, `chunk` return type: not inferred safely
- Line 1714, `__init__` return type: not inferred safely
- Line 1773, `_parse_probe` parameters: `resp`
- Line 1773, `_parse_probe` return type: not inferred safely
- Line 2257, `note` parameters: `msg`
- Line 2257, `note` return type: not inferred safely
- Line 1923, `nearest` parameters: `c`
- Line 1923, `nearest` return type: not inferred safely

## Totals

- Untyped parameters: 618
- Untyped returns: 237
- Reason: inferred type would require assumptions about caller contracts, external objects, or control-flow behavior.
