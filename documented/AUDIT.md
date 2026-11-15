# Pre-edit documentation audit

Generated from the original root-level Python files. The 'Needs comment?' column flags bodies for review; comments are only added for the cases allowed by `doc.md` (a)-(e).

## `archive_converter.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ToolError | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Codec | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| suffix | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Container | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Fmt | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| key | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| suffix | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| label | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Options | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Result | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| require | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| zstd_backend | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| human | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| BrotliReader | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| readable | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| readinto | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| close | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| BrotliWriter | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| writable | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| write | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| close | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| open_reader | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| open_writer | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read_full | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| peek | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_tar_block | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| codec_from_magic | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fmt_from_name | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| sniff | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| detect | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_target | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| level_spec | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| resolve_level | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_name | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| safe_path | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_zip | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_tar | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_7z | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_raw | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| entries | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| scrub | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| zip_time | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| pack_tar | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| pack_zip | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| pack_7z | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| pack | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| recode | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| staged | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| verify_output | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| streamable | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| convert_one | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| convert | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| report | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| module_available | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_formats | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `argparse2typer.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| ConversionError | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| literal | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| dotted | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_parser_ctor | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_parse_call | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_main_guard | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| py_name | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| q | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| kwargs_src | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| walk_scope | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| identifiers_in | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| string_lines | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Source | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| pos | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| start | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| end | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| seg | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| line_start | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| line_end | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| replace | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| delete_stmt | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| after_line | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| apply | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Ctx | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| warn | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| need | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| unique | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Param | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Cmd | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Ref | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Loc | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Analysis | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| convert_type | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| make_enum | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| help_expr | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| convert_add_argument | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| lit | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| dedupe | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| str_literal | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| locate_parser | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| analyze | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ordered | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_param | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_sig | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| app_def | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_kwargs | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| exclusive_checks | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_prologue | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| InlinePlan | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| plan_inline | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| apply_inline | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| find_entry | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| state_update | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_bridge | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| func | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| apply_bridge | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_leftovers | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| tidy | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| finalize | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| convert | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `autogp.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| load_config | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| setup_logging | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_cmd | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_inside_git_repo | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| git_repo_root | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_repo_clean | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| staged_changes_names | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| staged_changes_only_whitespace | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read_lines_safe | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| write_lines_safe | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ensure_gitignore | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| iter_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| looks_like_python_by_shebang | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_py_compile | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| detect_python_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_black_on_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| git_add_all | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| git_commit_and_push | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ensure_precommit | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `ax.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| lazy_property | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __get__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fscore | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| normalize | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_text_and_tail | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_full_text | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_domain | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_links | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_headings | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_language | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| generate_rule_dictionary | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_score_from_title_dict | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_meta_titles | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| sorted_title_candidates | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_title | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_title_index | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| general_ok_img | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| dimensions_ok | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_images | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_jsonld | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| edge_distance | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| different | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_content | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Article | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| title | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| image | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _date_and_article_text | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| publish_date | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| article_text | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| domain | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| language | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| full_text | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| code | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| links | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| heading | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extruct | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| jsonld | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| response_hash | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| microdata | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __repr__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| from_url | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| to_dict | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| view | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_article | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_url | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_url | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| re_count | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| make_regex | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| should_be_blocked | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Storage | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __len__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __repr__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| path | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| index_path | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| articles_path | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| index_path_exists | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add_articles | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| seen | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| articles | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| clear | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| drop_one | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove_hash_after_last_dash | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| UpfrontRobotFileParser | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| read | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Crawler | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| setup_seeds | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_robo_parser_domain | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| rp | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| can_fetch | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| robots_can_fetch | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| invalid_seeds | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| within_domain | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| pre_process_url | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| view_tree | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| return_seed_url_with_query_nonce | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_links | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| save_articles_from_links | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| start | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| MediumCrawler | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| SteemitCrawler | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| article | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| view_html | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `bibici.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_bibparser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| normalize_title | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| load_anthologies | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| log_message | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| err_message | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| similarity | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| norm_booktitle | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_year | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_month | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_pages | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_isbn | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _fix_based_on_isbn | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_issn | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_booktitle | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_address | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_author | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| search_crossref_for_doi | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_doi | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_field | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_article | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_book | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_inproceedings | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_techreport | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_phdthesis | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _doi_to_url | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _search_bib_from_doi | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| try_fix_with_doi | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cache_journal_issn | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cache_field | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| normalize_authors | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_database | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| look_for_misspellings | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_check | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_validate | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_check | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_validate | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `br.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ParallelBrotliCompressor | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| compress_file | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decompress_file | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| compress_bytes | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _iter_chunk_tasks | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _compress_worker | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CompressionResult | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| format_size | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| directory_size | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| compressed_output_path | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decompressed_output_path | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| relative_display | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _hash_file | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| compress_with_parallel | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decompress_with_parallel | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| tar_directory | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| compress_file_streaming | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decompress_file_streaming | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| untar_file | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| find_subdirs_to_tar | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| report_tar_result | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_subdirs_with_tar | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| should_compress_file | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| find_files_to_compress | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| find_files_to_decompress | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| collect_compress_targets | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| collect_decompress_targets | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_file_type_stats | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_jobs | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_decompressed_tars | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_results_rich | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_results_basic | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_dry_run | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_header | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| swallowed_directory | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `chafa.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| Color_space | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Dither2 | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Align | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _version_callback | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Mode | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Config | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| extract_colors | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| work_factor_int | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| rgb_to_din99d | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| brightness | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_term_256 | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Palette | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _distances | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| lookup | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| lookup2 | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| color_of | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _in_ranges | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _default_tags | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Symbol | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cov_from_bitmap | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _gen_braille | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _gen_sextant | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _octant_bits_to_cp | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _gen_octant | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| all_symbols | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Selector | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _parse_code_point | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_selectors_new | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| char_is_selected | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| apply_selectors | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_symbol_map | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _fill_bayer | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| gen_bayer_matrix | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _load_blue_noise | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Dither | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __post_init__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| apply_texture_dither | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fs_dither | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _align_dim | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| tuck_and_align | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _round_up_mult | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| boost_saturation | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _histogram_bounds | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _normalize_rgb | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| prepare_work_pixels | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _pack_colors | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _closest_popcount | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _find_blank_char | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _find_solid_char | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CellGrid | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_symbol_cells | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _apply_fill | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _apply_fill_fg_only | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _aix16fg | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _aix16bg | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _PrintCtx | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| queue | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| flush | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| reset_attrs | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| reset_fg | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _emit_attrs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _unpack | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _pen_transparent | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _sgr_fg | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _sgr_bg | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _sgr_fgbg | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _emit_attrs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_symbol_rows | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _scaled_placement | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rle_sixels | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _sixel_level | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_sixel | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_kitty | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_iterm | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| load_image | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| iter_frames | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_size | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_color | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_arg_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _detect_format | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _detect_colors | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `clean-py.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| Config | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Stats | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| setup_logging | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _has_fmt_off | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_file_mode | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove_duplicate_cells | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove_empty_cells | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove_magics | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| clean_python_code | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _print_diff | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _split_header | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _finalize_newlines | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| create_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| clean_py | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| clear_ipynb_output | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| clean_ipynb_cell | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| clean_ipynb | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _matches_exclude | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _load_gitignore | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| discover_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cache_key | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _load_cache | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _save_cache | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| load_config_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| find_config_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _git_changed_files | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _emit_error_github | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_doctor | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_linters | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| clean_stdin | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_watch | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Handler | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| on_any_event | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_all | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `compress_text.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CliError | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Codec | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Job | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Result | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _init_worker | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Reporter | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __call__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| sha256_file | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| part_path | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| zstd_encoder | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| zstd_decoder | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| brotli_encoder | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| brotli_decoder | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| bz2_encoder | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| bz2_decoder | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| xz_encoder | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| xz_decoder | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ppmd_encoder | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ppmd_decoder | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| stream_compress | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| compress_7z | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| verify_output | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_job | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| drain | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_jobs | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ensure_space | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_tar | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| resolve_algos | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_report | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_args | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `consolidate.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| ModuleInfo | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| LoadNames | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Name | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Lambda | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_FunctionDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| scan_defaults | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| LocalImportResolver | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| locate | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_ImportFrom | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Import | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| NameRewriter | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Name | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Attribute | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| SixImportRewriter | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| visit_ImportFrom | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Import | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| SixTransformer | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| visit_Attribute | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Call | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| OsPathTransformer | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_ospath | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Call | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Subscript | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ExecutorMethodRenamer | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Call | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ExecutorTransformer | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| visit_With | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| LoggingTransformer | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| visit_Assign | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Attribute | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Expr | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fail | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| unique_path | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_source | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| make_info | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| collect_python_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| load_directory | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| load_merged | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_future_import | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_six_import | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_logging_import | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_string_expr | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_named_assign | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _make_assign | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _wrap_path | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_executor_ref | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _module_imports_path_bare | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| strip_docstrings | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| inject_standard_imports | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fix_empty_bodies | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| defined_names | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| top_level_names | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| bound_import_names | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| unique_name | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| module_prefix | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| plan_renames | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| resolve_name | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| apply_renames | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| apply_transformer | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| merge_modules | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| dependencies | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| constant_name | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_main_guard | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_import_block | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_stdlib | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| unique_nodes | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| merge_imports | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| order_by_dependencies | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| emit | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_module | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| refactor | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `consolidator.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _version_callback | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ConsolidatorError | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| PlanWarning | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __str__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| BundledModule | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| archive_path | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Located | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _ThirdPartyPackage | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| ConsolidationPlan | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| summary | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_excluded | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _validate_dotted | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _flatten_search_paths | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _glob_match | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _walk_tree | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| recurse | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _module_name_for | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _read_bytes | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _human_size | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ScanResult | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _dynamic_import_target | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ImportScanner | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _add_strict_chain | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _add_loose | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _absolute_base | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Import | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_ImportFrom | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Call | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _scan_module | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _descend_package | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _locate | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _site_dirs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _resolve_thirdparty | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _find_package_dir | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _has_main_function | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _synthesize_main | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| analyze | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| warn | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| embed_module | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| embed_tree | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| dir_excluded | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| embed_thirdparty | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| embed_located | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| handle_import | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_scan | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ensure_main_module | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_data_spec | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collect_data | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| excluded | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| prune_pkg_dir | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| prune_dir | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _zip_timestamp | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _archive_members | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _render_header | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| write_output | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| consolidate | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _PackageDataAction | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __call__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _default_output_name | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `cron_explain.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| CronSpec | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| RunInstance | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| CronAnalysis | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_field | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| resolve_token | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_cron_line | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| explain_cron | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| explain_field_vals | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| set_to_str | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| matches_spec | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| compute_next_runs | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| analyze_overlaps | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| discover_sources | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `cronwhy.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| normalize_dow | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| humanize_list | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| field_summary | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_token | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_field | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CronSpec | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| month_wildcard | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| dom_wildcard | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| dow_wildcard | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_cron | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| matches | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| explain_spec | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| iter_next_runs | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_dst_quirky | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| dump_cron_file | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| detect_overlaps | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `csshtmlpret.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _version_callback | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _compile_props | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _prioritify | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _props_grouper | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| sort_properties | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove_empty_rules | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| condense_zero_units | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| condense_semicolons | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| wrap_css_lines | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add_encoding | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| normalize_whitespace | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| justify_right | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| split_long_selectors | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| simple_replace | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| css_prettify | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| prettify | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| html_prettify | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| html_prettify | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| walk2list | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_multiple_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| prefixer_extensioner | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_single_css_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_single_html_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| make_arguments_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `dedupe-py.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _version_callback | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _constant_name_variants | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_source | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| normalize_source | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _iter_target_py_files | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collect_definitions | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| analyze_files | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| write_repeated_json | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_refactor_map | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ASTStripper | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_FunctionDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_AsyncFunctionDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_ClassDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Assign | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| refactor_single_file | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_report | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_args | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `dupfx.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| Keep | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| file_stat_key | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| quick_hash | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| full_hash | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| iter_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| choose_keep | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `env_audit.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| UsageRecord | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Finding | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_dotenv | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| replace_var | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| UsageVisitor | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Subscript | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Call | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _handle_env_access | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _handle_call_access | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _extract_key | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| scan_python_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| scan_ci_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _extract_keys_from_text | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _extract_keys_from_dockerfile | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_secrets | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| reconcile | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_check | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_scan | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_init | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `env_stamp.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _version_callback | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Format | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| canonicalize_name | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _now_iso | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _version_tuple | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _version_change_scope | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run_quiet | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _partition | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| shannon_entropy | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| mask_url_credentials | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| redact_env | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| PythonInfo | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| OSInfo | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| PackageInfo | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Stamp | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| build | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _core_dict | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| to_dict | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| to_json | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| from_dict | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| stamp_digest | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _abiflags | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _executable_kind | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _libc_string_cached | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _libc_string | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _platform_tag | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _python_info | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _python_info_for | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _distro | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _locale_string | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _fs_case_sensitive | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _current_umask | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _os_info | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _venv_bin | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _site_packages | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _iter_dists | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _wheel_tag | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _origin | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _req_name | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_extra_only | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _package_infos | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _tool_versions | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| capture | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Change | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _name_list | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| diff_stamps | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_terminal | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_markdown | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_json | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Pin | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| spec | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| EnvProfile | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Candidate | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _Atomic | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _BudgetExhausted | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _pins_from_stamp | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _package_items | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _env_items | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _apply_package_items | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _apply_env_items | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _locate_interpreter | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _refusal_reasons | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _changes_from_items | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| BisectReport | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| exit_code | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Bisector | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| runs_used | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cache_hits | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run_inner | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| report | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| channel | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run_test | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cache_key | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _venv_for | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _sync | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _index_args | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _installer | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _child_env | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ddmin | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run_subset | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _log | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_allow_values | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _load_stamp | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_capture | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_diff | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_bisect | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _root | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_capture | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_diff | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_bisect | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `furl_cli.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| ic | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| callable_attr | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_iterable_but_not_string | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| lget | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| attemptstr | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| utf8 | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| non_string_iterable | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| idna_encode | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| idna_decode | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_valid_port | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| static_vars | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decorator | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| create_quote_fn | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| quote_fn | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_valid_encoded_path_segment | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_valid_encoded_query_key | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_valid_encoded_query_value | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_valid_scheme | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_valid_host | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_scheme | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| strip_scheme | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| set_scheme | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| has_netloc | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| urlsplit | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| urljoin | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| join_path_segments | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove_path_segments | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| quacks_like_a_path_with_segments | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Path | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| load | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| set | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| normalize | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| asdict | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| isabsolute | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| isabsolute | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| isdir | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| isfile | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __truediv__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __eq__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __ne__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __bool__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __str__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __repr__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _segments_from_path | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _path_from_segments | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| PathCompositionInterface | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| path | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| pathstr | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _force_absolute | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __setattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| URLPathCompositionInterface | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _force_absolute | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| FragmentPathCompositionInterface | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _force_absolute | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Query | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| load | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| set | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| params | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| params | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| encode | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| asdict | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __eq__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __ne__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __bool__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __str__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __repr__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _items | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _extract_items_from_querystr | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| QueryCompositionInterface | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| query | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| querystr | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __setattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Fragment | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| load | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| set | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| asdict | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __eq__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __ne__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __setattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __bool__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __str__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __repr__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| FragmentCompositionInterface | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fragment | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fragmentstr | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __setattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| furl | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| load | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| scheme | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| scheme | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| host | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| host | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| port | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| port | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| netloc | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| netloc | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| origin | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| origin | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| url | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| url | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| set | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| copy | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| tostr | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __setattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __str__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __repr__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __eq__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __ne__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `fuzzycmp.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _version_callback | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| default_tokenize | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| normalize_text | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ngrams | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| SimilarityMetric | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| similarity | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| distance | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __call__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __repr__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| EditDistanceMetric | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| edit_distance | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| similarity | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Levenshtein | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| edit_distance | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| DamerauLevenshtein | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| edit_distance | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Hamming | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| edit_distance | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Jaro | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| similarity | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| JaroWinkler | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| similarity | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __repr__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Jaccard | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| similarity | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Dice | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| similarity | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Cosine | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _vector | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| similarity | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| LCSSimilarity | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| lcs_length | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| similarity | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| NGram | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| similarity | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __repr__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| soundex | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| nysiis | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| metaphone | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _PhoneticMetric | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| code | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| similarity | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| SoundexMetric | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| NYSIISMetric | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| MetaphoneMetric | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _stable_hash | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| MinHash | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _shingles | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| signature | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| similarity | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __repr__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| SimHash | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fingerprint | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| similarity | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __repr__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| TokenSort | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| similarity | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| TokenSet | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| similarity | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| PartialRatio | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| similarity | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| FuzzyComparator | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _resolve | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _prep | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| similarity | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| distance | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| compare_all | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| best_match | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| matches_above | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| rank | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __call__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __repr__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| DiffOp | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __str__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| FileComparison | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| identical | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| report | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __str__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| align_lines | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _score | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _NormalizedWrapper | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| similarity | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| FileComparator | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _read | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| compare | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| diff_lines | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fuzzy_grep | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_similarity | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_compare_all | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_best_match | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_files | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_grep | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_phonetic | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _root | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_similarity | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_compare_all | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_best_match | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_files | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_grep | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_phonetic | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `get_sub.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ProgressBar | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| refresh | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| num_to_cn | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_name | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _print_and_choose | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| choose_archive | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| choose_subtitle | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| compute_subtitle_score | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| guess_subtitle | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_file_list | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_command | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| P7ZIP | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_list_output | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| namelist | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Video | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| sub_exists | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| delete_existed_subtitles | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Downloader | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| get_keywords | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_subtitles | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| download_file | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ZimukuDownloader | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| get_keywords | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_episode_page | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _get_archive_dowload_link | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_shooter_episode_page | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_subtitles | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| download_file | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ZimuzuDownloader | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| get_subtitles | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| download_file | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| DownloaderManager | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| get_downloader_by_name | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_downloader_by_choice_prefix | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| GetSubtitles | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_videos | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_search_results | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_archive | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_subtitle | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_result | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_video | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| start | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `git-prune-merged.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| Scope2 | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| GitError | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Branch | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Policy | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Options | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Scan | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| paint | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Console | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| say | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| warn | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fail | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Git | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| exec | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ok | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| text | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ref_exists | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| detect_base | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| worktree_branches | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| list_branches | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| merged_refs | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| matches | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| protect_reason | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| passes | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| squash_merged | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| scan | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| age | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| status | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| clip | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| line | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| command | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| restore_command | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| pick | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| choose | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| execute | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_options | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `git_trim.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _version_callback | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| GitError | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| run_git_ex | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_git | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| details_of | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _pipe | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_patch_ids | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Method | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| BranchRef | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| upstream_gone | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| age_days | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| last_commit_iso | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_refs | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| list_branches | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| current_branch | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| worktree_branches | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| git_dir | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_shallow | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ref_exists | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| rev_sha | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| merge_base | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| resolve_repo | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| resolve_base | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| PatchIdIndex | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| mainline_ids | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| branch_ids | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| combined_id | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| classify_branch | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Policy | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _split_patterns | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _match_any | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_protected | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| evaluate | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Analysis | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| candidates | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| analyze | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| trim_log_path | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| append_trim_log | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read_trim_log | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| delete_branches | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| warn_orphans | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _color_enabled | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| humanize_age | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| why_text | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _trunc | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_table | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fmt | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_scan_table | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| summary_line | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| branch_json | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| scan_json | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| common | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _local_base_name | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _analyze | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _reject_conflicting_flags | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_scan | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _preview_or_delete | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_delete | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_prune_remote | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_undo | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _restore_upstream | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _mkrepo | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _commit | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _commit_on | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _merge | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _squash_merge | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _mkorigin | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _demo_cli | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_demo | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| banner | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| note | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _need_git | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _scan | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _branch_map | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _exists | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_humanize_age | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_parse_refs | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_parse_patch_ids | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_is_protected_case_insensitive | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_split_patterns_and_match_any | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_render_table_truncates | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_plain_merge_classified_merged | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_single_commit_squash_detected | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_multicommit_squash_detected | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_rebase_merge_detected | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_unmerged_branch_is_unsafe_and_survives_delete | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_branch_in_worktree_is_skipped | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_current_branch_is_never_a_candidate | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_keep_patterns_and_defaults | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_dryrun_yes_undo_roundtrip | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_undo_restores_upstream_tracking | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_delete_pattern_filters | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_scan_exit_codes | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_scan_json_shape | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_gone_upstream_counts_as_no_upstream | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_prune_remote_deletes_merged_gone_and_undo_restores | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_stale_window_rules | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_shallow_clone_downgrades_to_unknown | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_orphan_upstream_warning | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_detached_head_scan_works | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_base_autodetect_master_and_origin_head | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_base_prefers_remote_when_strictly_ahead | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_undo_without_log | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_extra_unmerged_commit_keeps_branch_unmerged | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| test_demo_runs | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run2 | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _root | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_undo2 | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_demo2 | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `glow.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| ExitCode | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| GlowError | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| UsageError | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| InputError | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| DependencyError | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| ConversionError | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Options | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| writes_to_file | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| log_colorize | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Dependency | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| has_module | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| module_version | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| resolve_backend | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| char_width | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| strip_ansi | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visible_len | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| truncate_ansi | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| pad_ansi | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ansi | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Palette | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| get_palette | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| resolve_color | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| resolve_width | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| clamp_width | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| bootstrap_logging | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| configure_logging | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Text | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| CodeSpan | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Strong | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Emph | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Strike | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Link | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Image | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| SoftBreak | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| HardBreak | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| RawHtml | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Heading | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Paragraph | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| CodeBlock | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| QuoteBlock | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| ListItem | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| ListBlock | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| TableBlock | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| RuleBlock | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| HtmlBlock | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| normalise_label | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| collect_refs | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _strip_tags | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_inline | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add_text | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _list_kind | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _split_table_row | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _table_aligns | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _paragraph_line | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _starts_block | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_list | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_table | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_blocks | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_markdown | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Segment | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _merge_lines | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| inline_to_lines | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _group_chars | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| wrap_styled | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| segments_to_ansi | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _style_line | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| BuiltinRenderer | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _inline | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _wrapped | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rule | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _block | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _heading | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _code | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _quote | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _list | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _fit_columns | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _table | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cell_text | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cell_ansi | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| border | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| pad | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| row_line | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _html_block | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_markdown_builtin | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| safe_href | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _attr | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| inline_to_html | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| blocks_to_html | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _table_to_html | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| th | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| td | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| markdown_to_html_builtin | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _taskify_html | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| markdown_to_html | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| clean_html | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _code_language | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| html_to_markdown_markdownify | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| html_to_markdown_bs4 | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| collapse | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| inline | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| text_of | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| block | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_list | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_table | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| convert | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| html_to_markdown_plain | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Parser | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| handle_starttag | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| handle_endtag | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| handle_data | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| normalise_markdown | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| html_to_markdown | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| preprocess_for_rich | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_with_rich | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_markdown | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| separator_line | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Source | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Document | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| expand_sources | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decode_bytes | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read_source | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| detect_format | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_title | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| load_documents | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| html_body_fragment | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| standalone_html | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_to_html | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_to_markdown | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_render | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Pager | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _emit | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _dump | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _read_key | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _content_height | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _max_top | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _active_lines | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _render | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _status | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _handle_key | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _prompt | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _jump_match | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| write_output | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_doctor | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| GlowArgumentParser | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_args | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _silence_broken_pipe | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `hasdoc.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| iter_py_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _label_special | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| scan_comments | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_docstring_stmt | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| scan | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| first_stmt_docstring | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `html-minify.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| FileResult | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| minify_file | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| format_bytes | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| find_html_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_args | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `infer.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| infer_from_expr | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ReturnCollector | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_FunctionDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_ClassDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Lambda | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Yield | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Return | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _last_is_exit | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| infer_return | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| AnnotateTransformer | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _annotate_params | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_FunctionDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `json2md.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| Format | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Array_mode | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_header | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read_json | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| expand_arrays | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| flatten_object | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| make_rows | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| stringify | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| escape_markdown_cell | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_columns | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_table | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cell | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_list | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `json_merger.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| Conflict | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| schema_of | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| reject_nonstandard_constant | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read_json | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| merge_values | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| dedup_value | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| expand_inputs | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| new_output_path | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| write_json_exclusively | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_args | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `mhtml.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| Resource_handling | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Log_level | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ConversionProfile | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| ConverterConfig | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __post_init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| apply_profile | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| validate | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| to_dict | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ConverterLogger | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| set_progress_callback | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| log_progress | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| trace | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| debug | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| info | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| warning | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| exception | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| close | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _log | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ResourceType | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| classify_resource_type | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| resource_to_data_uri | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Resource | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __post_init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| resolve_resource_url | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| MHTMLParser | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _detect_encoding | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decode_text | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _decode_content | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _get_resource_type | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_mhtml | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _find_main_part | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _resource_from_part | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _extract_resources | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _extract_metadata | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _unique_resources | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _decode_header_value | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _extract_boundary | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_urls_from_content | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ResourceCache | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| put | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| clear | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cache_paths | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _write_disk | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _read_disk | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _SafeRedirectHandler | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| redirect_request | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ResourceManager | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| normalize_url | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _normalize_url | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _convert_to_data_uri | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _guess_content_type | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_resources_parallel | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _fetch_resource | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_safe_external_url | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _domain_matches | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_data_uri | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _get_resource_type | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| transform_resource | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CSSProcessor | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| rewrite_css | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_urls | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_css | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rewrite_tokens | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rewrite_import | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rewrite_url | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _function_url_value | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _string_representation | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _url_representation | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collect_urls | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collect_token_urls | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collect_import_url | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collect_nested_urls | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _conservative_minify | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rewrite_with_regex_fallback | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| replace | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| HTMLNode | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add_child | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| to_string | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| HTMLTreeParser | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| feed | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _convert_lxml_to_node | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _MHTMLCleaner | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _data_uri_media_type | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| sanitize_url | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_javascript_link | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| allow_element | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| HTMLProcessor | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_basic_html | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| to_string | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_resource_urls | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| rewrite_html | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| validate_html | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| minify_html | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _extend_safe_attributes | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _extract_rule_body | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_srcset | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rewrite_url | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rewrite_with_regex | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rewrite_with_regex_fallback | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ConverterPlugin | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| before_parse | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_resource | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| after_convert | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParsedDocument | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _CSSRewriteState | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| MHTMLConverter | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __enter__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __exit__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| close | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| convert | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _convert | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _convert_parsed | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _resolve_base_url | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _render_main_resource | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| convert_file | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| convert_stream | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| iter_convert | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _chunks | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run_before_parse_plugins | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run_after_convert_plugins | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _normalize_resource_map | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _extract_main_asset_urls | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _fetch_missing_resources | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _fetch_nested_css_resources | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _find_missing_css_urls | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _fetch_resource_batch | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _apply_resource_plugins | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rewrite_css_resources | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rewrite_css_resource | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rewrite_css_dependencies | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _resource_text | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _validate_output | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _fallback_conversion | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _unique_resources | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_argument_parser | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `mhtml_to_html.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _version_callback | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| MhtmlError | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| IndexPageNotFoundError | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| ConversionError | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| CssParseError | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| UrlError | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _lookup_codec | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decode_string | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _decode_inner | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| encode_string | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_charset | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_quotes | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| replace_charset | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_bom_charset | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decode_quoted_printable | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decode_binary | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decode_base64_bytes | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decode_base64 | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _decode_encoded_word_qp | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _decode_encoded_word | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decode_mime_header | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| flush | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _pct_encode | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_dot_segments | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _normalize_absolute | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| js_new_url | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _with_query_fragment | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _find_unescaped | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| normalize_location | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| resolve_path | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _test_content_type | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_document | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_stylesheet | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_text | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_image | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_plain_text | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_media | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_multipart | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_multipart_alternative | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_boundary | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_media_type | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_resource_uri | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _srcset_collect | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_srcset | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_space | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_descriptors | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| tokenize | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| serialize_srcset | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _format_srcset_density | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _css_is_ws_char | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _css_is_hex | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _css_is_digit | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _css_is_ident_start | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _css_is_ident_char | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _valid_escape_at | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ident_starts_at | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _number_starts_at | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _consume_ident | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _consume_escape | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ws_run_len | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _decode_escape_body | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _consume_bad_url_remnants | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CssToken | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| tokenize_css | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _consume_url_body | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _consume_number | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _consume_string | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| css_decode_string_token | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| css_decode_url_token | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| css_encode_string | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| css_encode_url | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CssNode | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| CssTok | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CssStr | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CssUrl | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CssFunc | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CssParen | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CssBracket | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CssOpPM | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CssComb | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| CssRaw | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CssDecl | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CssAtrule | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CssRule | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Toks | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| skip_ws | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| text | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_stylesheet | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_declaration_list | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_block_items | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _span_has_depth0_comma | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_atrule | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_block | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_rule | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_selector_prelude | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _match_nested_end | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _selector_drop_flag | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _selector_token_node | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_declaration_or_raw | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _raw_span_end | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_declaration_value | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _strip_trailing_ws | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _custom_property_end | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _extract_important | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _has_depth0_exclamation | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_components | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_nested | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _CssEmitter | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| token | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ws | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| result | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _norm_kind | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _emit_components | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _emit_raw | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _emit_block_items | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _emit_item | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| generate_stylesheet | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| generate_declaration_list | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| walk_css_nodes | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| walk_list | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| first_stylesheet_item_is_charset | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove_first_stylesheet_item | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| DomDocument | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| document_element | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| doctype | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| head | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_elements_by_tag_name | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| create_element | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| create_text_node | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| serialize | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _tag_name | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_dom | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _serialize_doctype | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| serialize_html_node | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _serialize_text_node | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _should_escape_raw_text | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _escape_entities_text | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _escape_raw_text | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _serialize_element | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _serialize_children | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _serialize_attributes | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| element_get_attribute | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| element_get_attribute_names | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| element_set_attribute | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| element_remove_attribute | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| element_append_child | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| element_remove | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| element_replace_with | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| element_prepend | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| element_after | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_meta_charset_elements | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_meta_content_type_elements | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Anomaly | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __post_init__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| to_dict | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Resource | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Mhtml | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _starts_with_markup | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_line_feed | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ends_with_crlf | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ends_with_lf | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _index_of_subarray | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _find_boundary_delimiter | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_header_line | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_mhtml | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_line | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| split_headers | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decode_raw_headers | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read_body_headers | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_header_line | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| init_resource | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| append_data | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_data_byte | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| truncate_data | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| truncate_data_line_terminator | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| flatten_data | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_resource_data | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| convert_embedded_mhtml | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_resource | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_stylesheet_charset | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_document_charset | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cache_resource_dom | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_resource_dom | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| release_resource_dom | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _js_json_stringify | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _get_original_url_function | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _get_resource | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _add_missing_resource | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _resolve_reference | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _set_attribute | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _escape_text | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _replace_stylesheet_urls | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| found | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| not_found | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| descend | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| found | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| not_found | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _create_document | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _get_page_info | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _convert_impl | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| descend | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| found | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| not_found | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| found_icon | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| not_found_icon | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| found_src | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| not_found_src | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| found_srcset | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| not_found_srcset | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| found_background | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| not_found_background | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| found_input | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| not_found_input | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| found_frame | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| not_found_frame | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _element_text_content | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _url_fragment | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| FetchConfig | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _is_blocked_address | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _validate_url_for_fetch | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _NoRedirect | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| redirect_request | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _read_capped | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _fetch_once | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_retry_after | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_transient_status | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _backoff_delay | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fetch_resource | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _fetch_with_redirects | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _fetch_resources | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| work | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ConvertConfig | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _with | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| convert | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _favicon_from_dict | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Favicon | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| PageData | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _fetch_and_convert | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _discover_inputs | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _default_output_path | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_arg_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _convert_file | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `mmdc.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| MermaidError | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| MermaidParseError | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| UnsupportedDiagramError | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _supports_color | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _red | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _yellow | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| warn | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| info | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| set_quiet | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| relative_to | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_cjk | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| char_width | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| text_width | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| wrap_text | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| unescape_entities | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| split_label_lines | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fit_label | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| lines_size | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_color | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| normalize_color | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Theme | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| get_theme | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Prim | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Canvas | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| prim_bbox | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| shift_prims | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| with_margin | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| center_text | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| left_text | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| shape_prims | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| rect | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| arrowhead | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| crosshead | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| transform_prims | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| transformed_size | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _xml_escape | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _num | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _pts_attr | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _prim_svg | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| canvas_to_svg | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _pdf_escape_text | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _pdf_color_ops | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rounded_rect_ops | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ellipse_ops | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _paint_op | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| canvas_to_pdf | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| X | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Y | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _font_candidates | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _find_pil_font | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _pil_color | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _dashed_segments | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| canvas_to_png | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| font_for | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| pt | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fade | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| draw_prim | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fill_c | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| stroke_c | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| strip_comments | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| split_statements | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_json_ish | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_init_directives | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_front_matter | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_acc | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| detect_diagram_type | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| preprocess_definition | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| FNode | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| FEdge | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| FGroup | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| FlowIR | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _read_head | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _read_pipe_label | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _match_op | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _scan_statement | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _split_amp | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _match_shape | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_node_ref | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_style_dict | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _group_id_for | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_flowchart | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ensure_node | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| layered_layout | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| positions | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| key | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| key | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _css_float | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _apply_style_dict | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _group_boxes | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| box_of | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _flow_node_size | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _flow_node_style | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _flow_edge_style | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _route_flow_edge | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _route_self_loop | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_flowchart | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| depth_of | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _transform_pts | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| SeqParticipant | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| SeqMessage | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| SeqNote | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| SeqActivation | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| SeqBlock | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| SeqElse | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| SeqIR | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _seq_flags | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_sequence | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cur | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| find_part | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _SeqState | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_sequence | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _seq_walk | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _seq_targets | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _seq_emit_bar | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _seq_open | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _seq_close | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _seq_activation | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _seq_block | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _seq_actor | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _seq_message | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _seq_endpoints | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| edge_x | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _seq_flags_apply | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _seq_note | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ClsClass | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| ClsRel | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| ClassIR | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _clean_cls_name | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_class | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cls_add_member | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cls_node_size | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cls_rel_style | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_class | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ErAttr | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| ErEntity | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| ErRel | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| ErIR | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_er | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _er_add_row | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_er | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| perp | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| StState | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| StTrans | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| StateIR | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_state | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ensure | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_state | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| depth_of | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| PieIR | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_pie | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_pie | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_diagram | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| renderMermaid | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| markdownImage | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _render_to_file | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| repl | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _pool_limiter | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| limiter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _positive_int | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _positive_float | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _output_value | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _CommanderParser | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| format_usage | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| format_help | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_parser | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _validate_icon_packs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cli | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `noneng.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| Detection | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Finding | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| LanguageDetector | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _result | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| detect | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| iter_text_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read_text_lines | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _display_path | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _has_enough_letters | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _live_view | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| scan_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| save_report | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `pipdown.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| Backend | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Link | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __post_init__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| clean_url | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parsed | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| filename | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_wheel | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_source | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_file | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| file_path | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __repr__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Package | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parsed_version | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| as_dict | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| TargetPython | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| supported_tags | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| version_str | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _compute_tags | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| HTMLIndexParser | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| handle_starttag | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_html | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_json | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fetch_page | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| strip_source_ext | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| python_matches | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| tag_priority | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| make_candidate | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| source_rank | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Finder | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| client | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| close | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __enter__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __exit__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| find | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collect | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _file_links | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _sort_key | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| pick_hash | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| file_hash | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| verify_hash | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| verify_archive | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remote_size | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| supports_ranges | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| download_single | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| download_parallel | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fetch_range | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| download_requests | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| download_pycurl | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| download_aria2c | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| download | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_version_arg | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_requirements_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| setup_logging | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `py-monolith.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _version_callback | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _guess_mime | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_font | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_image | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _data_uri | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _attr_escape | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _text_escape | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _escape_js | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _find_base | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _charset_from_ct | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| FetchResult | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| AssetFetchError | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| default_fetcher | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Rewriter | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _resolve | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_external_ref | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _inline_url | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| handle_decl | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| handle_comment | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| handle_pi | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| handle_starttag | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| handle_startendtag | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| handle_endtag | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| handle_data | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _dispatch | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _handle_link | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _handle_script | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _handle_style | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _handle_media | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _handle_iframe | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _handle_anchor | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _process_style_attr | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _inline_srcset | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _render_attrs | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _render | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Monolith | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| no_css | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| no_images | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| no_js | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| no_frames | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| no_links | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| no_metadata | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| no_audio | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| no_video | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| no_fonts | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| timeout | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| user_agent | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| base_url | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| isolate | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| encoding | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ignore_network_errors | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| insecure | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| make_dirs | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ok | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| html | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| data | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| save | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| from_string | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _process | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _fetch_main | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _fetch_bytes | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _fetch_data_uri | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _fetch_text | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _decode | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _process_html | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _inline_iframe | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _process_css | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| import_repl | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| url_repl | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _inject_meta | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ansi | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `pycleaner.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _c | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| bold | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cyan | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| yellow | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| red | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| green | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| dim | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _safe_read | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _display_path | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _iter_py_files | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| UnusedImport | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _ImportReport | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _collect_type_checking_lines | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collect_all_exports | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collect_used_names | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_import_names | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _analyse_imports | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _bound_name | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rewrite_simple_import | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rewrite_multiline_import | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rewrite_source | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _autoflake_one | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _scan_zip | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _scan_tar | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _inner | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _init_worker_imports | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _worker_file | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _worker_archive | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collect_import_targets | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_excluded | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _print_report | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_imports | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_imports_autoflake | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| DefItem | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _DefScanResult | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _is_const_name | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _DefCollector | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _add | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_FunctionDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_ClassDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Assign | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_AnnAssign | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _UsedNameCollector | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Name | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Attribute | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _scan_one_defs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _filter_unused_global | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _filter_unused_per_file | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_defs_from_source | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Remover | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _maybe_drop | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Module | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_ClassDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _extract_one | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_defs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_vulture_lines | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _comment_out | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _apply_vulture_fixes | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_vulture | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_target_func | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _replace_func_in_file | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_replace_func | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _read_block | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _find_block | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _already_has_import | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _top_import_end | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _replace_block_in_file | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_replace_block | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_parser | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `pydf.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | No | No | Module-level comments are not added unless a specific invariant needs explanation |
| Bar | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __len__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __str__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| format | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| DumbStatus | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| get_terminal_width_termios | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_terminal_width_resize | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_terminal_width_dumb | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_terminal_width | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| find_mountpoint | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| sanitize_output | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| out | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| hfnum | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| myformat | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| manglestring | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| makecolour | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_all_mountpoints | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| niceprint_fs | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_remote_fs | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_special_fs | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_row_mp | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_table | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| squeeze_table | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| display_table | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| display_termux_list | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `pydocgen.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| FunctionInfo | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| ClassInfo | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| ModuleInfo | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| is_empty | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ast_render_arg | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ast_render_args | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ast_function_signature | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ast_class_signature | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ast_collect | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_ast | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _libcst_code | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _libcst_suite_statements | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _libcst_parameters | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _libcst_function_signature | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _libcst_class_signature | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _libcst_decorators | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _libcst_collect | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_libcst | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ts_parser | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ts_text | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ts_signature | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ts_string_value | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ts_docstring | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ts_decorators | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ts_function | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ts_class | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ts_collect | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_tree_sitter | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _code_block | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _signature_lines | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_markdown | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| unique_path | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| save_markdown | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| module_display_name | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| markdown_parts | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| walk_python_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| expand_files | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| classify_targets | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_file_task | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_module_task | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| resolve_backend | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `pydup.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| Keep | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Mode | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Output_format | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Log_format | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _version_callback | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _xxh64_hexdigest | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _digest | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| FileEntry | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| DefinitionRef | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getstate__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __setstate__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| WorkerResult | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getstate__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __setstate__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| DuplicateGroup | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| NearPair | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| DefinitionGroup | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| SimilarPair | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| RunStats | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| ScanOutcome | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| CacheEntry | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| WorkerConfig | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getstate__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __setstate__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ScanConfig | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| SourceNormalizer | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _without_docstring | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _clear_type_params | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Module | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_FunctionDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_AsyncFunctionDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_ClassDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_arg | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Assign | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_AnnAssign | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_For | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_AsyncFor | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_With | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_AsyncWith | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_TypeAlias | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _SimpleGitignore | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _compile | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| matches | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _PathMatcher | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| matches | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _strip_lines | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _load_ignore_patterns | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _load_gitignore_patterns | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _RootFilter | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| ignored | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_root_filter | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_candidate_file | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_pyfiles | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _decode_source | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_source | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _filter_imports | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _normalize_tree | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| significant_tokens | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ngram_hashes | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _tree_hash | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _module_is_empty | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _definition_refs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _hash_fuzzy | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _hash_tlsh | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _shingle_values | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _minhash_signature | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _simhash64 | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _process_source | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _print_progress_line | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _notify | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run_serial | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _executor_class | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run_executor | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run_mp_pool | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run_joblib | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run_jobs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| update_progress | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _fingerprint_key | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _union_find | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _find | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _union | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _verify_candidates | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_groups | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _make_group | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_definition_groups | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_near_pairs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _sized_results | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ssdeep_pairs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _sweep_pairs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _tlsh_pairs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _simhash_pairs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _minhash_pairs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_similar_pairs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _write_simz | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _load_cache | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _save_cache | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _nonneg_int | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _mp_method_value | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _similarity_value | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_args | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _configure_logging | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _human_bytes | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _group_wasted | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _choose_keeper | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| path_key | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| variants | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _assign_keepers | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _load_baseline | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _filter_baseline | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _changed_since_paths | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _stat_entry | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _finalize_entries | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _split_paths | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collect_entries | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _print_groups_plain | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _print_groups_rich | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _iter_group_pairs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _group_payload | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _report_text | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _print_verbose_dry_run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _report_json | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _report_ndjson | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| emit | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _report_csv | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _stats_payload | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _report | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _interactive | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _prompt | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _guard_unchanged | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _quarantine_target | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _delete_one | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _write_manifest | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _manifest_record | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _apply_removals | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run_diff | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _resolve_fuzzy_methods | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _scan | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _validate_args | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _exit_code | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _emit_outcome | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _watch | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_scan | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| schedule | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Handler | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| on_any_event | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run2 | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `pyocr.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _version_callback | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| PyocrException | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| TesseractError | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CuneiformError | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| digits_only | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Box | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_xml_tag | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __str__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __box_cmp | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __lt__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __gt__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __eq__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __le__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __ge__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __ne__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __hash__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| LineBox | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| content | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_xml_tag | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __str__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __repr__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __contains__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __box_cmp | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __lt__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __gt__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __eq__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __le__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __ge__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __ne__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __hash__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| BaseBuilder | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read_file | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| write_file | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| start_line | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add_word | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| end_line | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_output | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| TextBuilder | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read_file | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| write_file | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| start_line | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add_word | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| end_line | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_output | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __str__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| DigitBuilder | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __str__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _WordHTMLParser | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __parse_confidence | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __parse_position | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| handle_starttag | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| handle_data | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| handle_endtag | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __str__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _LineHTMLParser | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| handle_starttag | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| handle_data | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| handle_endtag | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __str__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| WordBoxBuilder | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read_file | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| write_file | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| start_line | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add_word | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| end_line | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_output | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __str__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| LineBoxBuilder | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read_file | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| write_file | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| start_line | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add_word | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| end_line | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_output | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __str__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| DigitLineBoxBuilder | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __str__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CharBoxBuilder | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read_file | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| write_file | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __str__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _set_environment | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| psm_parameter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| tess_can_detect_orientation | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| tess_detect_orientation | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| tess_get_name | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| tess_get_available_builders | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| tess_run_tesseract | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| tess_cleanup | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ReOpenableTempfile | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __enter__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __exit__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| close | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| tess_image_to_string | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| tess_is_available | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| tess_get_available_languages | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| tess_get_version | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| PageSegMode | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Orientation | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| PageIteratorLevel | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| PolyBlockType | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| OSResults | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| raw_init | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_cleanup | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_is_available | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_get_version | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_get_available_languages | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_set_is_numeric | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_set_debug_file | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_set_page_seg_mode | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_init_for_analyse_page | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_set_image | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_recognize | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_analyse_layout | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_get_utf8_text | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_page_iterator_delete | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_page_iterator_next | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_page_iterator_is_at_beginning_of | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_page_iterator_is_at_final_element | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_page_iterator_block_type | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_page_iterator_bounding_box | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_page_iterator_orientation | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_get_iterator | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_result_iterator_get_page_iterator | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_result_iterator_get_utf8_text | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_result_iterator_get_confidence | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_detect_os | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_set_input_name | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_init_pdf_renderer | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_begin_document | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_add_renderer_image | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_end_document | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| libtess_can_detect_orientation | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| libtess_detect_orientation | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| libtess_get_name | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| libtess_get_available_builders | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _tess_box_to_pyocr_box | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| libtess_image_to_string | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| libtess_image_to_pdf | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| LibtesseractPdfBuilder | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| set_lang | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| set_output_file | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| set_text_only | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add_image | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __validate | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| libtess_is_available | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| libtess_get_available_languages | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| libtess_get_version | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cun_can_detect_orientation | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cun_get_name | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cun_get_available_builders | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cun_temp_file | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cun_image_to_string | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cun_is_available | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cun_get_available_languages | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cun_get_version | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_available_tools | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _load_image | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _print_result | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_tools | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_langs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_orientation | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_ocr | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _root | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_tools | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_langs | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_orientation | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_ocr | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `pyproj.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _version_callback | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Readme_format | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ProjectConfig | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| run | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| git_quiet | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| github_request | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_github_token | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_github_user | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| set_github_topics | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| create_github_repo | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_or_create_github_repo | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_py_version | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_pyproject | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_setup_py | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_setup_cfg | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_init_py | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_cli_py_typer | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_cli_py_argparse | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_cli_py_click | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_cli_py | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_single_file_py | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_cython_pyx | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_test | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_license | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_pre_commit | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_ci_github | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_ci_gitlab | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_dockerfile | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_dockerignore | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_mkdocs_yml | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_docs_index_md | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_sphinx_conf | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_docs_index_rst | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_readme | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_repo_templates | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| validate_name | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_pypi_availability | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_git_clean | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| scaffold_project | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| git_init_and_push | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| scrub_remote_token | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| rename_tree | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| replace_content | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| do_rename | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| prompt_missing | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _toml_value | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| load_config_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| apply_config | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| save_config | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_tree | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_next_steps | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_config_from_args | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_args | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `reformat_python.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _version_callback | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ReformatError | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| LogicalLine | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| head | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_code | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_decorator | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _logical_lines | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| close_current | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _finalise | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Frame | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __post_init__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _accepts_clause | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Indenter | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_clause | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_terminator | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _keyword_of | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _try_has_handlers | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _chain_is_continued | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _handle_decorator | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _handle_clause | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _matching_frame | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_with_map | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _sanitize | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_syntax | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Scope | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| bind | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| knows | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ScopeChecker | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _bind | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _bind_target | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collect_target_names | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collect_module_names | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _body | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _statement | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _expr | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| undefined_names | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| analyse | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _clause_candidates | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _clauses_after | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _region | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _depth | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _escape_candidates | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| reformat | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| attempt_error | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| failing_index | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error_position | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| reported_index | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| local_region | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| scope_pass | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| greedy_pass | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `reformatter.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ReformatterError | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| StructuralCounts | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| FileStats | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| BatchStats | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| LogicalChunk | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| ParsedLine | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| BlockFrame | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| SearchState | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| ReformatResult | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| is_compilable_python | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| collect_structural_counts | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| count_physical_lines | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _match_string_literal | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| isolate_shell_heredoc_bridges | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| split_top_level_physical_lines | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _find_hashes_outside_strings | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _tokenize_clean | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _reconstruct_slice | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _compute_depths | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _find_lambda_colons | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_statement_if | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _find_header_colon | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _indent_probe_block | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_header_probe | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_valid_simple_line | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _segment_simple_run | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| solve | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| segment_code_chunk | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| flush_simple | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| flush_simple_possible | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_logical_chunks | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _extract_expr_reads_and_attrs | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| collect_targets | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _extract_target_stores | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _analyze_condition | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| analyze_chunk | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _has_enclosing_func | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _has_enclosing_nonlocal_func | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _has_enclosing_async_func | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _has_enclosing_loop | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _loop_has_guard | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _can_pop_frame | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _pop_frames_with_try_lift | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _clear_read_vars | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _same_lexical_scope_vars | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _outer_defined_vars | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_chunk_at_indent | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_chunks | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_lookahead_requirements | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| infer_indents | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| register | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| reformat_source | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| reformat_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _natural_sort_key | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| discover_python_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| reformat_paths | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| format_stats_report | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_arg_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `remove_pages_of_pdf.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _validate | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _try_pypdf | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _try_pikepdf | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _try_pymupdf | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _pick_backend | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `replacer.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | No | No | Module-level comments are not added unless a specific invariant needs explanation |
| Symlink_policy | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Color | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _version_callback | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Options | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Rule | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| FileResult | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _colorize | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _safe_print | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _apply_case | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _make_replacement_fn | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fn | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_pattern | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _read_with_meta | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _atomic_write | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _file_allowed | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _load_ignore_patterns | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_ignored | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _walk_dir | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _iter_files | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _make_diff | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _save_for_undo | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _do_undo | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _git_is_clean | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _prompt_decision | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _line_of | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _process_one | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| rep | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _process_stdin | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _load_config | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _load_rules_file | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_rules | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_parser | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _merge_config | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _work | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `rlater.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| now_iso | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| die | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| warn | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| truncate | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| clean_ws | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| default_db_path | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Style | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __call__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| color_enabled | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| terminal_width | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| page | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| normalize_url | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_urls | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| dedupe | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| normalize_tags | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| FetchError | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| FetchOptions | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Page | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| decode_html | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fetch | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Node | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| attr | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| TreeBuilder | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| handle_starttag | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| handle_endtag | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| handle_data | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| iter_nodes | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| text_of | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| walk | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| raw_text | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| walk | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| class_tokens | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_noise | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| prune | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| text_stats | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| link_density | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| direct_text_len | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| find_main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| initial | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| has_block_child | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Renderer | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| flush | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| flat_text | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| markdown | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| walk | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| element | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| link | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| table_row | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_markdown | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| count_words | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Extracted | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| first_meta | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_plain | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| acquire | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| reading_minutes | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Hit | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| tidy_snippet | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Library | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _migrate | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| close | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| find | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| tags_of | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| all_tags | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| pick | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| save | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add_tags | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove_tags | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| apply_flag | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| mark_read | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| delete | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| search | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _search | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_terminal | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| strip_links | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| repl | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| wrap | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_blocks | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_ids | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| dedupe_ints | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fetch_options | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add_urls | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| work | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read_stdin_text | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_add | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_import | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_list | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| do_read | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_read | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_search | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_flag | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_tag | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_tags | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_rm | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_refetch | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_open | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| slugify | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| front_matter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_export | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_backup | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_stats | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| command | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `rmc.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| Status | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Mode | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Counts | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| FileResult | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| bytes_saved | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Job | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Options | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| write | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Summary | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _is_trivial | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _looks_like_code | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Stripper | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| on_leave | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_special | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _filter_lines | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_TrailingWhitespace | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_Param | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_FunctionDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_FunctionDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_ClassDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _register | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_docstring | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rewrite_smalls | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rewrite | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_IndentedBlock | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_SimpleStatementSuite | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_Module | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _decode | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _backup_target | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _make_backup | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_diff | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _process | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _init_worker | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_jobs | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _matches | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _has_python_shebang | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _wanted | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _walk | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| discover | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| summarize | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| log_results | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_text | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_json | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| exit_code | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| configure_logging | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _version_callback | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `rmc2.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _version_callback | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| TransformOptions | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _is_protected_comment | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_docstring_stmt | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _strip_docstring_seq | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _strip_docstring_body | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Transformer | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_TrailingWhitespace | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_EmptyLine | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_Module | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_ClassDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_FunctionDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _multiline_string_lines | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collapse_blank_lines | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _transform | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _read_source | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Result | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _process_one | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _worker | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _discover | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _color | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_parser | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _format_result | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| report | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `rmcfull.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| StripFileResult | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| ASTStripper | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _line_start | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _line_end | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _keep_comment | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collect_comments | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collect_docstrings | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| strip | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _apply | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _atomic_write | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| strip_file | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| make_diff | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| create_backup | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove_blank_lines | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| TSStripper | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collect_comments | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _libcst_strip | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _CommentRemover | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _keep | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_EmptyLine | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_TrailingWhitespace | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _DocstringRemover | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _is_docstring | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _strip_suite | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_Module | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_FunctionDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_ClassDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CSTStripper | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| strip | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| FileOptions | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Task | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| process_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| WalkFiles | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __call__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| should_skip | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_python_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_skip_dir | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| has_symlink_component | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| pathlib_walk_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| iter_python_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| iter_input_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_tasks | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| colorize_diff | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| rich_log | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| green | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `scraper.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| Engine | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Mode | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cprint | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_installed_packages | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| http_get | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| make_session | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read_lines | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| write_lines | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| strip_fragment | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| same_site | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fmt_bytes | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_robots | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _latest_from_mirror | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_pkg_updates | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_import_check | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _site_roots | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _iter_py | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _try_load | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _scrape_th18_from_site | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _render_coc_report | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_coc_links | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _yt_recent_videos | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _extract_th18_links | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _write_coc_youtube_report | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_coc_youtube | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_size_mb | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _quality_of | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _wanted_movie | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _movie_basic | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| save_state | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add_movie | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| crawl | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _movie_simple | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _crawl | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _sigint | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parallel_worker | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _movie_parallel | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| save_state | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| append | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_movie_crawl | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _img_urls_from_soup | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _same_host_links | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _check_image_size | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _download_image | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _hunt_images | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_image_hunt | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_image_save | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _crawl_site | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| allowed | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| same_domain | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_link_crawl | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _inspect_video_page | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_video_info | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_google_search | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _expand_srcset | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _head_size | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_web_size | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_pkg_updates2 | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_import_check2 | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_coc_links2 | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_coc_youtube2 | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_movie_crawl2 | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_image_hunt2 | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_image_save2 | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_link_crawl2 | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_video_info2 | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_google_search2 | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_web_size2 | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `shorten_names.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _protected | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _short_names | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _discover | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_registry | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _resolve_target | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _AliasRecord | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _ModuleFacts | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _NameCollector | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Name | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| collect | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _string_literal | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _string_list_literals | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _AllScanner | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_FunctionDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_FunctionDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_ClassDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_ClassDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Import | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_Import | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_ImportFrom | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_ImportFrom | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Name | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_all | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _mentions_all | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Assign | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_AnnAssign | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_AugAssign | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Del | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Call | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _extract_all | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _names_in_string_annotation | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Analyzer | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Import | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_Import | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_ImportFrom | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_ImportFrom | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Attribute | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_MatchClass | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Arg | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Call | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Annotation | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_FunctionDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_ClassDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_TypeAlias | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collect_type_parameters | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Name | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _analyze | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _follow_binding | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Plan | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _precompute_force_keep | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decide | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _decide_uncached | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| allocate_module_names | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| finalize_module | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Rewriter | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_Import | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_Import | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_ImportFrom | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_ImportFrom | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_Name | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rewrite_declaration | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_Global | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_Nonlocal | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| shorten_names | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _main | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `shortener.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| process_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `similarity_check.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _hash_shingle | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| discover_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read_source | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_stem_lexicon | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| normalise_stem | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _words | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _dotted | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _lex_tokens | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _shingles | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cosine_pairs | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| name_candidates | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| name_similarity | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| containment | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| approx_containment_pairs | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| final_score | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cluster | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| group_label | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_report | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| comp_avg | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_markdown | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| rect | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| text | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| score_color | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| squarify | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_svg | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| frame | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `sonic.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| Keep | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Output_format | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _version_callback | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fsz | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _CountingIterator | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __iter__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __next__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _version_key | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _human_key | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _month_key | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _numeric_key | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| SortKeyFactory | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _extract | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _normalize | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _primary | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __call__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| group_key | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| LineProcessor | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| log | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_file_size | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| LineReader | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _read_mmap | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _read_regular | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read_file | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read_stdin | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| LineFilter | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| apply | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| LineRangeFilter | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| apply | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| LineSorter | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| sort_in_memory | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _write_chunk | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| create_sorted_chunks | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _strip_lines | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| merge_sorted_files | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cleanup_temp_files | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _format_group_line | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _emit_group | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| coalesce | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| LineDeduplicator | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __call__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| unsorted | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| LineTransformer | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| apply | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| LineFormatter | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _line_sep | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| serialize | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _as_text | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _as_json | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _as_csv | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| OutputWriter | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| write | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| FileSorter | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_range | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_filter | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_transformer | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _dedupe_mode | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _dedupe_requested | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _write_stream | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_file | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run_check | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _write_stats_file | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_stats | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| save_report | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _take | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _reservoir_sample | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| FileAnalyzer | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| analyze_file | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_analysis | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_parser | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `sortplan.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| By | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| absolute | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| safe_id | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| utc_now | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| digest | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fingerprint | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| same_content | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| size_value | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| load_config | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| sniff | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _metadata_worker | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| metadata | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parsed_date | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _values | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| classify | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| destination | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| available | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| suffix_name | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| choose_destination | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| scan | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _atomic_json | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| sync_dir | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_plan | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| append_record | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read_records | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| repair_journal_tail | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| make_destination_dirs | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| move_noreplace | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _journal_path | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _apply_plan_unlocked | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _undo_plan_unlocked | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| operation_lock | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| apply_plan | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| undo_plan | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| last_plan_id | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| started | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_plan | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| make_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| state_arg | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| scan_args | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_plan | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_dedupe | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_apply | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_undo | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_explain | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `sr.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| Config | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| relpath | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| isbackup | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| line_matches | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| matches_glob | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| make_console | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| highlight_matches | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_match_line | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_context_line | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_only_matching | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| should_process_file | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| should_enter_dir | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| iter_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| walk_dir | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| search_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _search_file_json | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| replace_file | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _next_backup_path | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _version_callback | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _preprocess_legacy_flags | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _compile_or_die | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _load_ignore_file | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _display_file_types | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `strep.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| process_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_whl | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| collect_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| has_so | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_row | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `svgo.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| SvgoError | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| SvgoParserError | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __str__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| SvgoConfigError | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _js_round | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| js_to_fixed_str | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| js_to_fixed | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| tools_to_fixed | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| js_number_string | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove_leading_zero | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| js_parse_float | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| js_parse_int | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _read_number | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| PathDataItem | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_path_data | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _round_and_stringify | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _stringify_args | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| stringify_path_data | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cleanup_out_data | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| path2js | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| js2path | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| a2c | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _convert_relative_to_absolute | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _SubPathPoints | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _Points | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _gather_points | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add_point | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| to_absolute | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cross | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _convex_hull | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _dot | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _sub | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _minus | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _orth | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _support_point | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _process_simplex | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| paths_intersect | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| separated | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| TransformItem | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| transform2js | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _mth_rad | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _mth_deg | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _mth_cos | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _mth_sin | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _mth_tan | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _mth_acos | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _mth_asin | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _mth_atan | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _multiply_transform_matrices | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| transform_to_matrix | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| transforms_multiply | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| transform_arc | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _TransformParams | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _plain_round | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _smart_round | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _deg_round | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _float_round | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _transform_round | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| round_transform | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_identity_transform | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _create_scale_transform | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _merge_translate_and_rotate | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _optimize | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _decompose_qrab | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _decompose_qrcd | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| matrix_to_transform | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| js2transform | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| apply_matrix_to_path_data | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| transform_absolute_point | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| transform_relative_point | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Node | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Document | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __repr__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Element | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __repr__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| TextNode | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __repr__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CdataNode | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CommentNode | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| DoctypeNode | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| InstructionNode | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _expat_error_reason | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _line_offset | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_svg | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| flush_text | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| on_start | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| on_end | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _check_namespace_prefixes | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| on_text | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| on_comment | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| on_pi | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| on_cdata_start | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| on_cdata_end | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| on_start_doctype | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| on_end_doctype | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| on_default | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| StringifyOptions | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _encode_text_entities | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _encode_value_entities | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| stringify_svg | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _create_indent | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _stringify_node | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _stringify_element | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _stringify_attributes | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _stringify_text | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| detach_node_from_parent | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| map_nodes_to_parents | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CssDeclaration | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| CssRule | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _strip_css_comments | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _split_top_level | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_declarations | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| SelectorPart | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| CompoundSelector | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| unmatchable | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_compound | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_selector | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _selector_specificity | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| compare_specificity | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _part_matches | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _compound_matches | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _matches_sequence | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _match_rest | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| matches | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| query_selector_all | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _as_document | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collect_matches | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| query_selector | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| includes_attr_selector | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_stylesheet | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _strip_pseudo_classes | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _decode_uri | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| encode_svg_datauri | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decode_svg_datauri | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| find_references | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| includes_url_reference | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| includes_css_var_reference | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _script_event_attrs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_executable_url | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| has_scripts | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Stylesheet | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| collect_stylesheet | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collect_style_element | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| compute_own_style | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| compute_style | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| PluginConfig | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| BuiltinPlugin | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _remove_doctype | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_xml_proc_inst | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_comments | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _extract_attributes_in_stylesheet | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_deprecated_attrs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_attributes | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_metadata | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_editors_ns_data | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cleanup_attrs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _merge_styles | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _inline_styles | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| exit_root | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _extract_pseudo_string | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_rule_consumed | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _render_rules | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _minify_color_value | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _minify_number_token | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _minify_declaration_value | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _minify_declarations | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| minify_css | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _selector_cannot_match | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| minify_css_block | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _minify_styles | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| exit_root | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _has_string_prefix | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _generate_id | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _get_id_string | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cleanup_ids | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_id_preserved | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| exit_root | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collect_useful_nodes | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_useless_defs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _round_value | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cleanup_numeric_values | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _round_view_box_number | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _convert_rgb_to_hex | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _convert_colors | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| exit | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _apply_transforms_plugin | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _scale_numeric_attr | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _get_intersection | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_convex | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _get_cubic_bezier_point | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _get_distance | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _find_circle | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_arc | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _find_arc_angle | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_curve_straight_line | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _calculate_sagitta | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _make_longhand | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _reflect_point | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ConvertPathState | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _strong_round | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| round_fn | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _plain_round_fn | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _data2path | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| stringify | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _convert_to_relative | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _filters | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _arc_prev_fits | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _convert_to_mixed | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _convert_path_data | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_safe_to_remove | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_element_tables | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_unknowns_and_defaults | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| attribute_used_in_stylesheet | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_non_inheritable_group_attrs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_useless_stroke_and_fill | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| detect | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cleanup_enable_background | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| detect | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cleanup_value | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_hidden_elems | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| can_remove_non_rendering_node | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove_element | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| pre_pass | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| exit_root | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_empty_text | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _convert_shape_to_path | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _js_number_or_nan | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _any_nan | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _convert_ellipse_to_circle | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _move_elems_attrs_to_group | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| detect | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| exit | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _move_group_attrs_to_elems | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _has_animated_attr | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collapse_groups | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| exit | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _convert_transform | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _float_digits | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _define_precision | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_useless | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _convert_to_shorts | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| convert | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_empty_attrs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_empty_containers | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| exit | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| exit_root | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _merge_paths | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| element_has_url | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| update_previous_path | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_unused_ns | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| exit | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _sort_attrs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_ns_priority | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| compare | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _sort_defs_children | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| compare | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_desc | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_title | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_style_element | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_raster_images | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_scripts | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_namespace_aware_elem | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| exit | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_view_box | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_dimensions | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_xmlns | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _add_attributes_to_svg_element | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _add_classes_to_svg_element | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cleanup_list_of_values | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| round_values | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _convert_one_stop_gradients | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| exit | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _convert_style_to_attrs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _prefix_ids | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_basename | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| escape_identifier_name | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| generate_prefix | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| prefix_id | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| prefix_reference | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_attributes_by_selector | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_attrs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_elements_by_attr | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_off_canvas_paths | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _remove_xlink | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| find_prefixed_attrs | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| exit | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _reuse_paths | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enter | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| exit | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _noop_plugin | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _invoke_plugins | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _make_preset | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| preset_fn | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _get_plugin | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _resolve_plugin_config | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _import_config | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| load_config | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| optimize | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _CliUsageError | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _HelpRequested | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _VersionRequested | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _CliExit | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _CliOptions | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| finalnewline | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _edit_distance | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _suggest_similar | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _unknown_option | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _option_display | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _plural | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_cli_args | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| consume_variadic | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| assign_value | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _colors_enabled | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _red | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _green | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _print_error | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _print_line | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _write_stdout | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _show_available_plugins | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _check_is_dir | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _iter_svg_file_descriptions | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _format_os_error | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _optimize_file_task | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _get_executor | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _shutdown_executor | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _RunState | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _handle_task_result | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _print_profit | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _process_file_descriptions | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_task | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| flush | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _optimize_folder | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _process_svg_data | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _write_output | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run_cli | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _flush_stdout_safe | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `tcg.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| get_clipboard_content | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_language_from_extension | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| replace_shebang | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| archive_existing_file | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| create_symlink | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `tcleaner.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| expand_and_resolve | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_exempt | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_size | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| purge_system | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `tcs.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| die | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| send_to_clipboard | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _all_identifiers | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _unsafe_names | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _name_stats | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _short_names | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_rename_mapping | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CompressTransformer | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _r | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_docstring_expr | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_docstring_stmt | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _strip_body | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_Module | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _strip_funcdef | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_FunctionDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_AsyncFunctionDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_ClassDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_Param | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_AnnAssign | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_ImportFrom | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_SimpleStatementLine | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_SimpleStatementSuite | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_IndentedBlock | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_Name | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _shrink_indent | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| compress_source | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_python_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| strip_x | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_string_expr | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_docstring_stmt | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_docstring_small | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_future_import_line | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| strip_suite | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| XStripper | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| leave_TrailingWhitespace | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_EmptyLine | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_Module | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_ClassDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_FunctionDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_AsyncFunctionDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_payload | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `termux_cleaner.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| expand_and_resolve | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| purge_system | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `tmc.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| SkipFile | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Mode | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| StripFileResult | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _is_trivial | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _looks_like_code | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Stripper | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| on_leave | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_special | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _filter_lines | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_TrailingWhitespace | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_FunctionDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_AsyncFunctionDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| visit_ClassDef | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_docstring | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rewrite_smalls | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rewrite | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_IndentedBlock | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| leave_SimpleStatementSuite | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| strip | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| strip_file | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| make_diff | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| create_backup | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove_blank_lines | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| FileOptions | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Task | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _init_worker | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_tasks | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| WalkFiles | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __call__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| DiscoveryIssue | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| record_discovery_error | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_skip_dir | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_python_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_excluded | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| pathlib_walk_files | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _fastwalk_parent_ok | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| iter_python_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| iter_input_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| write_json_report | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| colorize_diff | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| make_rich_ui | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| emit | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| configure_logging | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| green | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| entrypoint | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `tokei.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| Sort | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Output | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _version_callback | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Stats | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| add | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| as_dict | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Block | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Quote | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Syntax | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _ParseState | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _split_lines | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Parser | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _scan_line | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _block_advance | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _string_advance | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _with_prefix | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _end_re | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parser | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _b | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _q | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ext | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| count_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _shebang_language | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| detect_language | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _count_one | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _Rule | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _translate_gitignore | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_rules | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| IgnoreFile | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| load | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| match | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ExcludePatterns | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| excluded | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ancestor_ignores | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Options | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _walk_dir | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| keep | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| iter_input_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run_jobs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| gather | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _terminal_columns | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _sorted_items | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _total | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_terminal | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| row | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_json | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _key | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_yaml | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_toml | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_csv | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_arg_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _canonical_types | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _print_languages | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `tscleaner.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| ProcessResult | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _make_parser | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| detect_language | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_error | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_valid | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| collect_comment_ranges | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _html_raw_text_blocks | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _html_embedded_comments | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove_comment_ranges | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove_blank_lines | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| process_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_candidate | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| find_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_table | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `vcf2json.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| split_unquoted | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decode_text | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_parameters | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_property | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_vcards | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| write_json | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| write_csv | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `vsvg.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| Renderer | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Engine | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Color | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _version_callback | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _f | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _hsl_to_rgb | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cu | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_color | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_length | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| M | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| __matmul__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| apply | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| scale | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_transform | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| PathError | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _PP | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ws | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| more | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| num | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| flag | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cubic | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _quad | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _arc | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| angle | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| flatten_path | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| newsub | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ensure | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _seg_count | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ellipse_pts | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rounded_rect_pts | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cubic | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| shape_subpaths | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _disc | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _dashes | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _stroke_run | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| stroke_polygons | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _local | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _style | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Warnings | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| empty | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| text | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_view_box | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| root_matrix | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| intrinsic_size | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Doc | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _walk | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _bbox | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _edges_of | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _sweep | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _fill_evenodd | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| span | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _fill_nonzero | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _mask_polys | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| span | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _blend_mask | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _downsample | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| rasterize | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| encode_png | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| chunk | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _paeth | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decode_png | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| TermInfo | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _probe | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_probe | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _term_size | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| composite_rgb | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _to256 | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_blocks | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_sixel | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| nearest | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_kitty | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| paste_center | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| load_cairosvg | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cairosvg_render | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cairosvg_png | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _auto_ss | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| emit | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| export_png | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| note | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `wget2.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| Wget2Error | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| UsageError | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| ConfigError | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| PolicyError | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| NetworkError | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| TransportError | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| RequestTimeout | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| HTTPStatusError | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| IntegrityError | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| ChecksumError | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| FilesystemError | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Interrupted | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| RangeNotSupportedError | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| StopStream | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| exit_code_for | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| InterruptFlag | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| set | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| clear | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_set | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| redact_url | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _replace | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| redact_text | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Logger | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| close | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _format | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| emit | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| debug | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| info | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| warning | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| human_size | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_size | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_rate | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_int_list | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_http_date | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_content_range | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| format_duration | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| url_host | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| url_basename | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| sanitize_filename | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| filename_from_url | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| content_disposition_filename | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| validate_url | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| safe_unlink | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ensure_directory | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fsync_dir | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| preallocate | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| atomic_finalize | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| apply_mtime | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| unique_path | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ensure_free_space | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_netrc | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| plan_ranges | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| merge_intervals | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| covered_bytes | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| subtract_interval | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| split_segments | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| PartState | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| state_path | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| load | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| to_dict | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| save | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| maybe_save | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| discard | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| mark | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| resume_offset | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| missing | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| done_bytes | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| matches_remote | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| open_part_state | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| TokenBucket | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _refill | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| consume | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| MultiLimiter | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| enabled | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| consume | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| RetryPolicy | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| attempts | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| delay_for | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| should_retry | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ChecksumSpec | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __str__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_checksum | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_checksums | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| load_checksum_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| digest_file | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| verify_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Response | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| header | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| int_header | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ProbeResult | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| header | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| last_modified_ts | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| CookieJar | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| update | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| header_for | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| load_file | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| save | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| RequestOptions | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| with_credentials | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| credentials_for | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _map_curl_error | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| HttpClient | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_headers | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| request | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| on_header | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| on_write | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| probe | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| DiskSink | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| feed | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| checkpoint | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| MemorySink | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| feed | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| reset | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| ProgressReporter | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add_file | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| update | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _print_plain | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| finish | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| clear_line | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| log | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| close | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _total | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| OutputOptions | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Settings | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| retry_policy | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| effective_connections | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| global_limiter | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| connection_limiter | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| specs_for | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Resolution | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| skipped | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _SafeDict | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __missing__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| validate_template | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_template | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| template_values | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| resolve_output | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| write_manifest | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| HostLimiter | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _semaphore | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| slot | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| PathLocks | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _lock_for | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| hold | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Result | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| ok | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| to_dict | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Downloader | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _limiters | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _sleep | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _attempt | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _execute | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _transfer | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cleanup_failure | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _download_segments | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| worker | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _fetch_segment | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _commit_memory | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _download_single | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _check_ranged_response | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _verify_complete | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Crawler | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _allowed | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _selected | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| discover | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| collect | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Runner | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _client | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| work | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| worker | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _config_from_argv | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _load_config | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _d | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _install_interrupt | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| handler | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| restore | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _read_input | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _parse_method_data | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _build_settings | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _progress_mode | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _summary_exit | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _print_summary | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _report | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_check | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `whoiser.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _version_callback | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Rawtext | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Color | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Shell_completion | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| QueryResult | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| RateLimiter | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| wait | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| redact_key | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| valid_date | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| is_empty_field | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| purge_empty_fields | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _format_text | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| format_text | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_dotted | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| set_dotted | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| del_dotted | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| select_fields | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| flatten | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| encode_domain | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cache_path | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cache_get | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cache_put | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| default_cache_dir | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| load_config | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| load_dotenv | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| find_api_key | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_url_for_target | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fetch | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| postprocess_record | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| parse_response | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| query_one | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| diff_records | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| resolve_color | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| wrap_output | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_json | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| render_record | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| format_single_result | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| write_csv_results | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| write_output | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _write_to_stream | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| collect_domains | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read_domains_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read_domains_stdin | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| execute_targets | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_check_key | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_config | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| version_full | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| generate_manpage | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| generate_completion | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `xreport.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| fsz | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| detect_archive | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _gzip_stream_size | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _zstd_frame_size | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| analyze_archive | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_archive | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_scan | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _count_stream | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _h_tar | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _h_zstd | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _h_xz | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _h_gzip | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _h_bz2 | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _h_7z | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _h_zip | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_sizes | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `xtar.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_compression_type | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| BrotliStreamReader | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __init__ | method | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| readable | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _fill | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| read | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| zstd_reader | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decompression_stream | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| open_tar | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_integrity | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| scan_archives | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| nested_candidates | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_archive | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| find_archives | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cli | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `xztool.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| _version_callback | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| Report | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Report2 | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _try_import | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| BackendUnavailableError | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| Options | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| effective_workers | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| FileResult | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| ok | method | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| SubdirResult | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| default_workers | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| human_size | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| dir_size | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _now_stamp | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _normalize_exts | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _excluded | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run_jobs | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| count_by_ext | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| find_compress_targets | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| find_decompress_targets | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _xz_filters | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _compress_xz_file | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _lzma_alone_chunk | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| compress_bytes_lzma | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _lzma_dechunk | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decompress_bytes_lzma | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| compress_bytes_7z | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decompress_bytes_7z | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| make_tar | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| make_tar_xz | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| verify_tar_xz | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| extract_tar_payload | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _extract_members | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _extract_tar_dir | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _output_parent | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| compress_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| result | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decompress_file | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| result | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _compress_job | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _decompress_job | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _archive_job | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _progress_line | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_file_compression | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_file_decompression | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| run_tar_subdirs_then_compress | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| archive_subdirectories | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| pack_directory | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| report_file_results | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _rich_report | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _configure_logging | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_compress | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_decompress | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_archive | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _cmd_pack | function | No | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _root | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_compress | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_decompress | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_archive | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_pack | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `xztools.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| Backend | class | Yes | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _Args | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| __getattr__ | method | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _ParserShim | class | No | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| parse_args | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_help | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| print_usage | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _FallbackLogger | class | No | No | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| info | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| warning | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| error | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| success | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| debug | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| remove | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| format_size | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| dir_size | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _is_skipped_dir | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _matches_exclude | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| discover_files | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| compress_bytes | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| decompress_bytes | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| FileResult | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| _worker_compress_xz | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _worker_decompress_xz | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _worker_tar_dir | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _read_file_bytes | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _pylzma_compress_blob | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _pylzma_decompress_blob | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _worker_pylzma_7z | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _worker_pylzma_un7z | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _pylzma_compress_chunk | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _chunked_compress_file | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _chunked_decompress_file | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _worker_lzma_chunk_compress | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _worker_lzma_chunk_decompress | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _print_file_result | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _print_summary | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _tar_top_level_dirs | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_compress | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_decompress | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_tar_dirs | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_archive_cwd | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _collect_7z_targets | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add_compress_tree | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| add_decompress_tree | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_7z | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_lzma_chunk | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _add_common_xz_opts | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| build_parser | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| _run | function | No | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_compress2 | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_decompress2 | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_tar_dirs2 | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_archive_cwd2 | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd__7z | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| cmd_lzma_chunk2 | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |

## `zip_crack.py`

| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |
|---|---|---|---|---|---|
| module | module | Yes | Yes | No | Module-level comments are not added unless a specific invariant needs explanation |
| CrackResult | class | Yes | Yes | Review | Check for non-obvious invariants, ordering, or resource lifetime before adding a comment |
| elapsed | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| pps | method | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| format_duration | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| check_password_batch | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| get_wordlist_batches | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| count_lines | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| brute_force_zip | function | Yes | No | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
| main | function | Yes | Yes | Review | Check for (a)-(e) cases; no comment is added without a specific, supported rationale |
