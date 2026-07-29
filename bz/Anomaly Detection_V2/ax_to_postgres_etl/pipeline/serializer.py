"""
Serializer for ETL pipeline.

Handles value normalization, validation, and COPY format serialization.
"""

import io
import csv
from typing import List, Tuple, Any, Optional
from dataclasses import dataclass


@dataclass
class SerializationResult:
    """Result of serialization operation."""
    content: str
    rows_serialized: int
    rows_skipped: int
    errors: List[str]


class RowDataError(Exception):
    """Error in row data that should skip the row."""
    pass


def normalize_value(value: Any) -> Any:
    """
    Normalize a value for PostgreSQL COPY text format.
    
    Rules:
    - None → None (PostgreSQL NULL)
    - str → valid UTF-8 str
    - bytes/bytearray → decoded to str
    - bool → 't' or 'f'
    - int/float → str representation
    
    Args:
        value: The value to normalize
        
    Returns:
        Normalized value suitable for COPY
    """
    if value is None:
        return None
    
    if isinstance(value, str):
        # Verify round-trip is safe
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            value = value.encode("utf-8", errors="replace").decode("utf-8")
        return value
    
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    
    if isinstance(value, bytearray):
        return bytes(value).decode("utf-8", errors="replace")
    
    if isinstance(value, memoryview):
        return value.tobytes().decode("utf-8", errors="replace")
    
    if isinstance(value, bool):
        return "t" if value else "f"
    
    return str(value)


def validate_row_encoding(row: List[Any], columns: Optional[List[str]] = None) -> Tuple[bool, Optional[str]]:
    """
    Validate that all values in a row can be safely encoded to UTF-8.
    
    Args:
        row: List of values to validate
        columns: Optional list of column names for error messages
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    for idx, val in enumerate(row):
        if val is None:
            continue
        try:
            if isinstance(val, bytes):
                val.decode("utf-8")
            else:
                str(val).encode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError) as e:
            col_name = columns[idx] if columns and idx < len(columns) else f"col_{idx}"
            return False, (
                f"column={col_name}, "
                f"value={repr(val)[:100]}, "
                f"type={type(val).__name__}, "
                f"error={e}"
            )
    return True, None


def serialize_rows(
    rows: List[List[Any]], 
    columns: Optional[List[str]] = None,
    encoding_error_policy: str = "fail"
) -> SerializationResult:
    """
    Serialize rows to COPY format.
    
    Args:
        rows: List of rows to serialize
        columns: Optional column names for validation
        encoding_error_policy: 
            - "fail": raise exception on encoding error
            - "reject_row": skip row with encoding error
            - "replace": replace invalid characters
            
    Returns:
        SerializationResult with content and statistics
    """
    output = io.StringIO(newline="")
    writer = csv.writer(
        output,
        delimiter="\t",
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\n",
    )
    
    rows_serialized = 0
    rows_skipped = 0
    errors = []
    
    for row_idx, row in enumerate(rows):
        # Validate row encoding
        is_valid, error_msg = validate_row_encoding(row, columns)
        
        if not is_valid:
            if encoding_error_policy == "fail":
                raise RowDataError(f"Row {row_idx}: {error_msg}")
            elif encoding_error_policy == "reject_row":
                errors.append(f"Row {row_idx}: {error_msg}")
                rows_skipped += 1
                continue
            # For "replace", continue with normalization
        
        # Normalize values
        normalized_row = [normalize_value(val) for val in row]
        
        # Check column count
        if columns and len(normalized_row) != len(columns):
            if encoding_error_policy == "fail":
                raise RowDataError(
                    f"Row {row_idx}: Column count mismatch "
                    f"(expected {len(columns)}, got {len(normalized_row)})"
                )
            elif encoding_error_policy == "reject_row":
                errors.append(
                    f"Row {row_idx}: Column count mismatch "
                    f"(expected {len(columns)}, got {len(normalized_row)})"
                )
                rows_skipped += 1
                continue
        
        # Serialize row
        writer.writerow(normalized_row)
        rows_serialized += 1
    
    return SerializationResult(
        content=output.getvalue(),
        rows_serialized=rows_serialized,
        rows_skipped=rows_skipped,
        errors=errors,
    )


def build_copy_buffer(
    rows: List[List[Any]], 
    col_count: int, 
    log_func=None,
    columns: Optional[List[str]] = None
) -> Tuple[str, int]:
    """
    Build tab-delimited COPY buffer from rows.
    
    This is a backward-compatible wrapper around serialize_rows.
    
    Args:
        rows: List of rows to serialize
        col_count: Expected number of columns
        log_func: Optional logging function
        columns: Optional column names for validation
        
    Returns:
        Tuple of (content_string, skipped_row_count)
    """
    result = serialize_rows(rows, columns, encoding_error_policy="reject_row")
    
    if log_func and result.errors:
        for error in result.errors[:10]:  # Log first 10 errors
            log_func(f"  WARNING: {error}")
        if len(result.errors) > 10:
            log_func(f"  WARNING: ... and {len(result.errors) - 10} more errors")
    
    return result.content, result.rows_skipped
