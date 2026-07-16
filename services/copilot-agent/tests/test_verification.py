"""Exhaustive matrix for the deterministic citation checker (P3.2).

``app.verification`` re-validates every claim's ``source_refs`` against the
CACHED tool results from the conversation (``list[app.planner.ToolCallTrace]``)
-- no LLM, no I/O, no clock. See the module docstring in ``app.verification``
for the design decisions this matrix exercises: the ``asserted_value``
extension to ``SourceRef``, the positional ``tool_call_id``/``record_id``
scheme, the quarantine-redaction fail-closed behavior, and the conservative
type-coercion rules.

Hermetic and fully deterministic: no fixtures touch a real Ollama/OpenEMR.
"""

from __future__ import annotations

from app.planner import ToolCallTrace
from app.quarantine import (
    QuarantinedSummarizer,
    QuarantineSummary,
    quarantine_tool_result,
)
from app.schemas.common import MedicationStatus, SourceRef
from app.schemas.planner import ToolName
from app.schemas.tools import MedicationItem, MedicationsOutput
from app.schemas.verification import Claim
from app.verification import (
    CacheIndex,
    CitationStatus,
    check_claim,
    check_claims,
    check_source_ref,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _trace(result: dict | None, *, error: str | None = None, tool: ToolName = ToolName.GET_MEDICATIONS) -> ToolCallTrace:
    return ToolCallTrace(tool=tool, args={}, result=result, error=error)


def _ref(
    *,
    tool_call_id: str = "call_0",
    record_id: str = "0",
    field: str = "status",
    asserted_value: str | None = None,
) -> SourceRef:
    return SourceRef(tool_call_id=tool_call_id, record_id=record_id, field=field, asserted_value=asserted_value)


_MEDS_RESULT = {
    "items": [
        {"name": "Lisinopril", "dose": "10mg", "status": "active", "start_date": "2024-01-01", "end_date": None},
        {"name": "Atorvastatin", "dose": "20mg", "status": "discontinued", "start_date": "2020-05-01", "end_date": "2023-01-01"},
    ]
}

_PATIENT_SUMMARY_RESULT = {"patient_id": 42, "first_name": "Jane", "medication_count": 3}

_QUARANTINE_WRAPPED_RESULT = {
    "data": {"items": [{"name": "[free-text summarized separately]", "status": "active"}]},
    "summary": "Patient is on one active medication.",
}


# ---------------------------------------------------------------------------
# CacheIndex.from_trace -- positional tool_call_id / record_id scheme
# ---------------------------------------------------------------------------


def test_empty_trace_yields_empty_index():
    index = CacheIndex.from_trace([])

    result = check_source_ref(_ref(asserted_value="active"), index)

    assert result.status is CitationStatus.UNKNOWN_TOOL_CALL


def test_tool_call_ids_are_positional_in_trace_order():
    index = CacheIndex.from_trace([_trace(_MEDS_RESULT), _trace(_PATIENT_SUMMARY_RESULT)])

    first_call = check_source_ref(_ref(tool_call_id="call_0", record_id="0", field="status", asserted_value="active"), index)
    second_call = check_source_ref(
        _ref(tool_call_id="call_1", record_id="0", field="first_name", asserted_value="Jane"), index
    )

    assert first_call.status is CitationStatus.VALID
    assert second_call.status is CitationStatus.VALID


def test_list_shaped_result_indexes_items_positionally():
    index = CacheIndex.from_trace([_trace(_MEDS_RESULT)])

    first_item = check_source_ref(_ref(record_id="0", field="status", asserted_value="active"), index)
    second_item = check_source_ref(_ref(record_id="1", field="status", asserted_value="discontinued"), index)

    assert first_item.status is CitationStatus.VALID
    assert second_item.status is CitationStatus.VALID


def test_single_object_result_is_one_record_at_id_zero():
    index = CacheIndex.from_trace([_trace(_PATIENT_SUMMARY_RESULT)])

    result = check_source_ref(_ref(record_id="0", field="medication_count", asserted_value="3"), index)

    assert result.status is CitationStatus.VALID


def test_quarantine_wrapped_result_is_unwrapped_via_data_key():
    index = CacheIndex.from_trace([_trace(_QUARANTINE_WRAPPED_RESULT)])

    status_field = check_source_ref(_ref(record_id="0", field="status", asserted_value="active"), index)
    redacted_field = check_source_ref(_ref(record_id="0", field="name", asserted_value="Lisinopril"), index)

    assert status_field.status is CitationStatus.VALID
    assert redacted_field.status is CitationStatus.REDACTED_FIELD


def test_errored_call_registers_the_tool_call_id_with_zero_records():
    index = CacheIndex.from_trace([_trace(None, error="not_found")])

    result = check_source_ref(_ref(record_id="0", field="status", asserted_value="active"), index)

    # The call happened (id known) but produced no records -- distinct from
    # a ref naming a call that was never made (UNKNOWN_TOOL_CALL).
    assert result.status is CitationStatus.UNKNOWN_RECORD


# ---------------------------------------------------------------------------
# check_source_ref -- structural resolution failures
# ---------------------------------------------------------------------------


def test_unknown_tool_call_id_fails():
    index = CacheIndex.from_trace([_trace(_MEDS_RESULT)])

    result = check_source_ref(_ref(tool_call_id="call_99", asserted_value="active"), index)

    assert result.status is CitationStatus.UNKNOWN_TOOL_CALL


def test_non_numeric_record_id_fails():
    index = CacheIndex.from_trace([_trace(_MEDS_RESULT)])

    result = check_source_ref(_ref(record_id="med-1", field="status", asserted_value="active"), index)

    assert result.status is CitationStatus.UNKNOWN_RECORD


def test_negative_record_id_fails():
    index = CacheIndex.from_trace([_trace(_MEDS_RESULT)])

    result = check_source_ref(_ref(record_id="-1", field="status", asserted_value="active"), index)

    assert result.status is CitationStatus.UNKNOWN_RECORD


def test_out_of_range_record_id_fails():
    index = CacheIndex.from_trace([_trace(_MEDS_RESULT)])

    result = check_source_ref(_ref(record_id="2", field="status", asserted_value="active"), index)

    assert result.status is CitationStatus.UNKNOWN_RECORD


def test_unknown_field_fails():
    index = CacheIndex.from_trace([_trace(_MEDS_RESULT)])

    result = check_source_ref(_ref(record_id="0", field="nonexistent_field", asserted_value="x"), index)

    assert result.status is CitationStatus.UNKNOWN_FIELD


def test_redacted_field_fails():
    index = CacheIndex.from_trace([_trace(_QUARANTINE_WRAPPED_RESULT)])

    result = check_source_ref(_ref(record_id="0", field="name", asserted_value="Lisinopril"), index)

    assert result.status is CitationStatus.REDACTED_FIELD


def test_missing_asserted_value_fails_closed():
    index = CacheIndex.from_trace([_trace(_MEDS_RESULT)])

    result = check_source_ref(_ref(record_id="0", field="status", asserted_value=None), index)

    assert result.status is CitationStatus.NO_ASSERTED_VALUE


def test_null_cached_field_value_is_a_mismatch():
    index = CacheIndex.from_trace([_trace(_MEDS_RESULT)])

    result = check_source_ref(_ref(record_id="0", field="end_date", asserted_value="2025-01-01"), index)

    assert result.status is CitationStatus.VALUE_MISMATCH


def test_non_scalar_cached_field_value_is_a_mismatch():
    index = CacheIndex.from_trace([_trace({"items": [{"tags": ["a", "b"]}]})])

    result = check_source_ref(_ref(record_id="0", field="tags", asserted_value="a"), index)

    assert result.status is CitationStatus.VALUE_MISMATCH


# ---------------------------------------------------------------------------
# check_source_ref -- valid citation
# ---------------------------------------------------------------------------


def test_valid_citation_passes():
    index = CacheIndex.from_trace([_trace(_MEDS_RESULT)])

    result = check_source_ref(_ref(record_id="0", field="status", asserted_value="active"), index)

    assert result.status is CitationStatus.VALID
    assert result.passed is True


# ---------------------------------------------------------------------------
# Type-coercion edges (Q3)
# ---------------------------------------------------------------------------


def test_string_case_insensitive_match():
    index = CacheIndex.from_trace([_trace({"items": [{"name": "Lisinopril"}]})])

    result = check_source_ref(_ref(record_id="0", field="name", asserted_value="lisinopril"), index)

    assert result.status is CitationStatus.VALID


def test_string_whitespace_insensitive_match():
    index = CacheIndex.from_trace([_trace({"items": [{"name": "Lisinopril"}]})])

    result = check_source_ref(_ref(record_id="0", field="name", asserted_value="  Lisinopril  "), index)

    assert result.status is CitationStatus.VALID


def test_string_mismatch():
    index = CacheIndex.from_trace([_trace({"items": [{"name": "Lisinopril"}]})])

    result = check_source_ref(_ref(record_id="0", field="name", asserted_value="Atorvastatin"), index)

    assert result.status is CitationStatus.VALUE_MISMATCH


def test_enum_value_case_insensitive_match():
    index = CacheIndex.from_trace([_trace(_MEDS_RESULT)])

    result = check_source_ref(_ref(record_id="0", field="status", asserted_value="Active"), index)

    assert result.status is CitationStatus.VALID


def test_enum_value_mismatch():
    index = CacheIndex.from_trace([_trace(_MEDS_RESULT)])

    result = check_source_ref(_ref(record_id="0", field="status", asserted_value="discontinued"), index)

    assert result.status is CitationStatus.VALUE_MISMATCH


def test_int_string_matches_int_value():
    index = CacheIndex.from_trace([_trace(_PATIENT_SUMMARY_RESULT)])

    result = check_source_ref(_ref(record_id="0", field="medication_count", asserted_value="3"), index)

    assert result.status is CitationStatus.VALID


def test_float_string_matches_int_value_via_numeric_equality():
    index = CacheIndex.from_trace([_trace(_PATIENT_SUMMARY_RESULT)])

    result = check_source_ref(_ref(record_id="0", field="medication_count", asserted_value="3.0"), index)

    assert result.status is CitationStatus.VALID


def test_numeric_string_mismatch():
    index = CacheIndex.from_trace([_trace(_PATIENT_SUMMARY_RESULT)])

    result = check_source_ref(_ref(record_id="0", field="medication_count", asserted_value="4"), index)

    assert result.status is CitationStatus.VALUE_MISMATCH


def test_non_numeric_string_against_numeric_value_is_a_mismatch():
    index = CacheIndex.from_trace([_trace(_PATIENT_SUMMARY_RESULT)])

    result = check_source_ref(_ref(record_id="0", field="medication_count", asserted_value="a lot"), index)

    assert result.status is CitationStatus.VALUE_MISMATCH


def test_date_string_exact_match():
    index = CacheIndex.from_trace([_trace(_MEDS_RESULT)])

    result = check_source_ref(_ref(record_id="0", field="start_date", asserted_value="2024-01-01"), index)

    assert result.status is CitationStatus.VALID


def test_date_string_mismatch():
    index = CacheIndex.from_trace([_trace(_MEDS_RESULT)])

    result = check_source_ref(_ref(record_id="0", field="start_date", asserted_value="2024-02-02"), index)

    assert result.status is CitationStatus.VALUE_MISMATCH


def test_date_truncated_to_date_only_does_not_match_full_timestamp():
    """Conservative-by-design: no date-specific parsing/truncation."""
    index = CacheIndex.from_trace([_trace({"items": [{"date": "2026-06-01T09:00:00"}]})])

    result = check_source_ref(_ref(record_id="0", field="date", asserted_value="2026-06-01"), index)

    assert result.status is CitationStatus.VALUE_MISMATCH


def test_bool_true_string_matches_true():
    index = CacheIndex.from_trace([_trace({"items": [{"flag": True}]})])

    result = check_source_ref(_ref(record_id="0", field="flag", asserted_value="true"), index)

    assert result.status is CitationStatus.VALID


def test_bool_false_string_matches_false():
    index = CacheIndex.from_trace([_trace({"items": [{"flag": False}]})])

    result = check_source_ref(_ref(record_id="0", field="flag", asserted_value="FALSE"), index)

    assert result.status is CitationStatus.VALID


def test_bool_string_mismatch_wrong_value():
    index = CacheIndex.from_trace([_trace({"items": [{"flag": True}]})])

    result = check_source_ref(_ref(record_id="0", field="flag", asserted_value="false"), index)

    assert result.status is CitationStatus.VALUE_MISMATCH


def test_bool_string_invalid_token_is_a_mismatch():
    index = CacheIndex.from_trace([_trace({"items": [{"flag": True}]})])

    result = check_source_ref(_ref(record_id="0", field="flag", asserted_value="yes"), index)

    assert result.status is CitationStatus.VALUE_MISMATCH


# ---------------------------------------------------------------------------
# check_claim -- AND semantics across multiple source_refs
# ---------------------------------------------------------------------------


def test_claim_with_single_valid_citation_passes():
    index = CacheIndex.from_trace([_trace(_MEDS_RESULT)])
    claim = Claim(
        text="The medication is active.",
        source_refs=[_ref(record_id="0", field="status", asserted_value="active")],
    )

    result = check_claim(claim, index)

    assert result.passed is True
    assert len(result.citation_results) == 1


def test_claim_with_single_invalid_citation_fails():
    index = CacheIndex.from_trace([_trace(_MEDS_RESULT)])
    claim = Claim(
        text="The medication is discontinued.",
        source_refs=[_ref(record_id="0", field="status", asserted_value="discontinued")],
    )

    result = check_claim(claim, index)

    assert result.passed is False


def test_claim_with_multiple_refs_all_valid_passes():
    index = CacheIndex.from_trace([_trace(_MEDS_RESULT)])
    claim = Claim(
        text="Started 2024-01-01, currently active.",
        source_refs=[
            _ref(record_id="0", field="start_date", asserted_value="2024-01-01"),
            _ref(record_id="0", field="status", asserted_value="active"),
        ],
    )

    result = check_claim(claim, index)

    assert result.passed is True
    assert all(r.passed for r in result.citation_results)


def test_claim_with_multiple_refs_one_invalid_fails_the_whole_claim():
    """AND semantics: one bad citation sinks an otherwise-valid claim."""
    index = CacheIndex.from_trace([_trace(_MEDS_RESULT)])
    claim = Claim(
        text="Started 2024-01-01, currently discontinued.",
        source_refs=[
            _ref(record_id="0", field="start_date", asserted_value="2024-01-01"),
            _ref(record_id="0", field="status", asserted_value="discontinued"),
        ],
    )

    result = check_claim(claim, index)

    assert result.passed is False
    # Both citations are still reported -- not short-circuited -- for P3.3.
    assert len(result.citation_results) == 2
    assert result.citation_results[0].passed is True
    assert result.citation_results[1].passed is False


def test_claim_with_zero_citations_reaching_the_checker_fails_closed():
    """Not reachable via normal ``Claim`` construction (P3.1's min_length=1),
    but nothing prevents a caller from bypassing validation -- the vacuous
    ``all([])`` trap must not silently verify a claim with no citations."""
    claim = Claim.model_construct(text="Unsupported claim.", source_refs=[])
    index = CacheIndex.from_trace([])

    result = check_claim(claim, index)

    assert result.citation_results == []
    assert result.passed is False


# ---------------------------------------------------------------------------
# check_claims -- batch
# ---------------------------------------------------------------------------


def test_check_claims_on_empty_list_returns_empty_list():
    index = CacheIndex.from_trace([_trace(_MEDS_RESULT)])

    assert check_claims([], index) == []


def test_check_claims_reports_mixed_pass_fail_for_multiple_claims():
    index = CacheIndex.from_trace([_trace(_MEDS_RESULT)])
    passing = Claim(
        text="Active.",
        source_refs=[_ref(record_id="0", field="status", asserted_value="active")],
    )
    failing = Claim(
        text="Discontinued.",
        source_refs=[_ref(record_id="0", field="status", asserted_value="discontinued")],
    )

    results = check_claims([passing, failing], index)

    assert [r.passed for r in results] == [True, False]


# ---------------------------------------------------------------------------
# Integration: real quarantine output feeds the checker (documents the
# REDACTED_FIELD consequence -- see app.verification module docstring,
# decision 3).
# ---------------------------------------------------------------------------


class _FakeOllama:
    def extract(self, messages: list[dict[str, str]], schema: type, *, options=None):
        assert schema is QuarantineSummary
        return QuarantineSummary(summary="One active medication, Lisinopril.")


def test_real_quarantined_medication_result_redacts_name_but_keeps_status():
    summarizer = QuarantinedSummarizer(ollama_client=_FakeOllama())
    output = MedicationsOutput(
        items=[
            MedicationItem(
                name="Lisinopril",
                dose="10mg",
                route="oral",
                status=MedicationStatus.ACTIVE,
                start_date="2024-01-01",
            )
        ]
    )
    quarantined = quarantine_tool_result(summarizer, ToolName.GET_MEDICATIONS, output)
    index = CacheIndex.from_trace([_trace(quarantined)])

    status_citation = check_source_ref(_ref(record_id="0", field="status", asserted_value="active"), index)
    name_citation = check_source_ref(_ref(record_id="0", field="name", asserted_value="Lisinopril"), index)

    assert status_citation.status is CitationStatus.VALID
    assert name_citation.status is CitationStatus.REDACTED_FIELD
