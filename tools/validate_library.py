#!/usr/bin/env python3
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import add_component as component
import kicad_lib_merge as merge


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYMBOLS = os.path.join(ROOT, "Avionics_Symbols.kicad_sym")
FOOTPRINTS = os.path.join(ROOT, "Avionics_Feet.pretty")
MODELS = os.path.join(ROOT, "Models")


def footprint_name(text):
    _, end, _, pos = component.parse_form(text, ("footprint", "module"))
    pos = merge._skip_space(text, pos, end - 1)
    if pos >= end - 1:
        raise component.ValidationError("footprint is missing its name")
    if text[pos] == '"':
        return merge._string(text, pos, end - 1)[0]
    return merge._atom(text, pos, end - 1)[0]


def validate():
    errors = []
    warnings = []
    footprint_names = set()
    footprint_models = set()

    try:
        symbol_text = merge.read(SYMBOLS)
        symbols, order = merge.split_tops(symbol_text)
    except (OSError, merge.MergeError) as exc:
        return [f"symbol library: {exc}"], warnings

    try:
        filenames = sorted(name for name in os.listdir(FOOTPRINTS)
                           if name.endswith(".kicad_mod"))
    except OSError as exc:
        return [f"footprint library: {exc}"], warnings
    for filename in filenames:
        expected = filename[:-len(".kicad_mod")]
        footprint_names.add(expected)
        path = os.path.join(FOOTPRINTS, filename)
        try:
            text = merge.read(path)
            found = footprint_name(text)
            if found != expected:
                errors.append(f"{filename}: footprint name is {found!r}")
            if "(model " in text:
                footprint_models.add(filename)
        except (OSError, component.ValidationError, merge.MergeError,
                IndexError) as exc:
            errors.append(f"{filename}: {exc}")

    for name in order:
        try:
            reference = component.props(symbols[name]).get("Footprint", "")
        except (component.ValidationError, merge.MergeError) as exc:
            errors.append(f"symbol {name}: {exc}")
            continue
        if not reference:
            continue
        if not reference.startswith("Avionics_Feet:"):
            warnings.append(f"symbol {name}: external footprint {reference!r}")
            continue
        target = reference.split(':', 1)[1]
        if target not in footprint_names:
            errors.append(f"symbol {name}: missing footprint {target!r}")

    try:
        models = sorted(name for name in os.listdir(MODELS)
                        if name.lower().endswith((".step", ".stp")))
    except OSError as exc:
        errors.append(f"model directory: {exc}")
        models = []
    if models and not footprint_models:
        warnings.append(f"{len(models)} models are present but no existing "
                        "footprints reference them")
    print(f"validated {len(order)} symbols, {len(filenames)} footprints, "
          f"and {len(models)} models")
    return errors, warnings


def main():
    parser = argparse.ArgumentParser(prog="validate_library")
    parser.add_argument("--warnings-as-errors", action="store_true")
    args = parser.parse_args()
    errors, warnings = validate()
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if errors or (warnings and args.warnings_as_errors):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
