use byteorder::{LittleEndian, WriteBytesExt};
use pyo3::prelude::*;
use pyo3::types::PyDict;
use jwalk::WalkDirGeneric;
use std::collections::HashSet;
use std::fs::File;
use std::io::Read;
use ring::digest::{Context, SHA256};
use rayon::prelude::*;

/// Finds a candidate sentence boundary near a given byte position in UTF-8 text.
///
/// Searches backward up to `byte_window` bytes (adjusted forward to a UTF-8 character boundary)
/// and looks for the last occurrence of common sentence delimiters. If a delimiter is found,
/// the function returns the byte index immediately after that delimiter; otherwise it returns
/// `byte_pos`.
///
/// Recognized delimiters: "\n\n", ". ", "! ", "? ", ".\n", "!\n", "?\n".
///
/// # Examples
///
/// ```
/// let text = "First sentence. Second sentence!\nThird sentence?\n\nFourth.";
/// // pick a byte position inside "Second sentence!"
/// let pos = text.find("Second").unwrap() + 10;
/// let boundary = get_sentence_boundary(text, pos, 160);
/// // boundary should point just after the delimiter of the previous sentence ("First sentence. ")
/// assert_eq!(&text[boundary..boundary + 6], "Second");
/// ```
fn get_sentence_boundary(text: &str, byte_pos: usize, byte_window: usize) -> usize {
    let mut search_start = if byte_pos > byte_window { byte_pos - byte_window } else { 0 };
    
    // Ensure search_start is on a char boundary by moving forward if necessary
    while search_start < byte_pos && !text.is_char_boundary(search_start) {
        search_start += 1;
    }
    
    let region = &text[search_start..byte_pos];
    
    let delims = ["\n\n", ". ", "! ", "? ", ".\n", "!\n", "?\n"];
    
    let mut best_idx: Option<usize> = None;
    for delim in delims {
        if let Some(idx) = region.rfind(delim) {
            let actual_idx = search_start + idx + delim.len();
            if best_idx.is_none() || actual_idx > best_idx.unwrap() {
                best_idx = Some(actual_idx);
            }
        }
    }
    
    best_idx.unwrap_or(byte_pos)
}

/// Produces overlapping text chunks snapped to nearby sentence boundaries.
///
/// The function splits `text` into sequential chunks of up to `chunk_size_chars` characters, snaps each chunk end forward to a nearby sentence boundary when possible, and yields per-chunk Python dictionaries suitable for Python consumption.
///
/// Parameters:
/// - `py`: Python GIL token (omitted from external docs; required for PyO3 interop).
/// - `text`: Source string to chunk.
/// - `chunk_size_chars`: Maximum number of characters for each chunk before snapping to a sentence boundary.
/// - `chunk_overlap_chars`: Number of characters each chunk should overlap with the next chunk.
/// - `prefix`: String prepended to the `text_preview` value in each chunk dictionary.
/// - `base_offset`: Integer added to character indices to produce returned `start_offset` and `end_offset`.
///
/// Returns:
/// A `Vec<PyObject>` where each element is a Python `dict` with keys:
/// - `start_offset`: character index (plus `base_offset`) where the chunk begins,
/// - `end_offset`: character index (plus `base_offset`) where the chunk ends,
/// - `text_preview`: a preview string consisting of `prefix` followed by the chunk text.
///
/// # Examples
///
/// ```
/// use pyo3::prelude::*;
/// // Acquire the GIL and call the function as shown; unwrap for brevity in examples.
/// let gil = Python::acquire_gil();
/// let py = gil.python();
/// let text = "Hello world. This is a small test. It contains multiple sentences.";
/// let chunks = create_chunks(py, text, 20, 5, "...", 0).unwrap();
/// assert!(!chunks.is_empty());
/// ```
#[pyfunction]
fn create_chunks(py: Python, text: &str, chunk_size_chars: usize, chunk_overlap_chars: usize, prefix: &str, base_offset: usize) -> PyResult<Vec<PyObject>> {
    let mut chunks = Vec::new();
    
    let char_indices: Vec<usize> = text.char_indices().map(|(b, _)| b).collect();
    let total_chars = char_indices.len();
    
    if total_chars == 0 {
        return Ok(chunks);
    }

    let mut start_char = 0;
    while start_char < total_chars {
        let raw_end_char = std::cmp::min(start_char + chunk_size_chars, total_chars);
        let raw_end_byte = if raw_end_char < total_chars { char_indices[raw_end_char] } else { text.len() };
        
        let mut end_byte = raw_end_byte;
        if raw_end_char < total_chars {
            end_byte = get_sentence_boundary(text, raw_end_byte, 160); 
            if end_byte <= char_indices[start_char] {
                end_byte = raw_end_byte;
            }
        }
        
        let start_byte = char_indices[start_char];
        let chunk_text = &text[start_byte..end_byte];
        
        let dict = PyDict::new(py);
        dict.set_item("start_offset", base_offset + start_char)?;
        let chunk_char_len = chunk_text.chars().count();
        dict.set_item("end_offset", base_offset + start_char + chunk_char_len)?;
        dict.set_item("text_preview", format!("{}{}", prefix, chunk_text))?;
        
        chunks.push(dict.into());

        if end_byte == text.len() {
            break;
        }

        let chunk_char_len = chunk_text.chars().count();
        let end_char = start_char + chunk_char_len;
        let next_start = if end_char > chunk_overlap_chars {
            end_char - chunk_overlap_chars
        } else {
            end_char
        };

        // Ensure we always advance by at least 1 character to avoid infinite loops
        start_char = if next_start > start_char {
            next_start
        } else {
            start_char + 1
        };
        }
    
    Ok(chunks)
}

/// Locate a nearby sentence boundary and return its character index.
///
/// Searches up to `char_window` characters before `char_pos` for common sentence delimiters
/// (double newlines, `. `, `! `, `? ` and the same followed by `\n`) and selects the last delimiter
/// end that lies at or before `char_pos`. If no delimiter is found within the search window,
/// the resulting position corresponds to `char_pos` (or the end of the text if `char_pos` is past the end).
///
/// Arguments:
/// - `text`: the UTF-8 text to search.
/// - `char_pos`: target character position around which to search for a boundary.
/// - `char_window`: number of characters to look backwards from `char_pos` when searching for a boundary.
///
/// # Returns
///
/// The character index of the chosen sentence boundary (counting Unicode scalar values).
///
/// # Examples
///
/// ```
/// let s = "Hello world. This is a test.";
/// // The boundary after "Hello world. " is at character index 13
/// assert_eq!(find_sentence_boundary(s, 20, 10), 13);
/// ```
#[pyfunction]
fn find_sentence_boundary(text: &str, char_pos: usize, char_window: usize) -> usize {
    let mut byte_pos = text.len();
    let mut byte_search_start = 0;
    
    let target_start_char = if char_pos > char_window { char_pos - char_window } else { 0 };
    
    let mut current_char_idx = 0;
    for (b_idx, _) in text.char_indices() {
        if current_char_idx == target_start_char {
            byte_search_start = b_idx;
        }
        if current_char_idx == char_pos {
            byte_pos = b_idx;
            break;
        }
        current_char_idx += 1;
    }

    let region = &text[byte_search_start..byte_pos];
    let delims = ["\n\n", ". ", "! ", "? ", ".\n", "!\n", "?\n"];
    
    let mut best_byte_idx: Option<usize> = None;
    for delim in delims {
        if let Some(idx) = region.rfind(delim) {
            let actual_idx = byte_search_start + idx + delim.len();
            if best_byte_idx.is_none() || actual_idx > best_byte_idx.unwrap() {
                best_byte_idx = Some(actual_idx);
            }
        }
    }
    
    let final_byte_idx = best_byte_idx.unwrap_or(byte_pos);
    text[..final_byte_idx].chars().count()
}

/// Scan folders in parallel and return a flat list of matching file paths.
///
/// This function recursively walks each provided folder in parallel, skipping hidden entries
/// and without sorting. Files are filtered by extension using a case-insensitive comparison
/// against the supplied `extensions` (each extension should include the leading dot, e.g. ".txt");
/// if `extensions` is empty all files are included. For each matching file the function attempts
/// to canonicalize the path; on success the canonical path is returned with a leading `\\?\`
/// Windows prefix removed if present, otherwise the original path string is returned when possible.
///
/// # Examples
///
/// ```
/// let folders = vec!["./src".to_string()];
/// let extensions = vec![".rs".to_string()];
/// let result = scan_folders(folders, extensions).unwrap();
/// // result is a Vec<String> of file paths (may be empty)
/// assert!(result.iter().all(|p| !p.is_empty()));
/// ```
#[pyfunction]
fn scan_folders(folders: Vec<String>, extensions: Vec<String>) -> PyResult<Vec<String>> {
    let ext_set: HashSet<String> = extensions.into_iter().map(|e| e.to_lowercase()).collect();
    
    let results: Vec<Vec<String>> = folders.into_par_iter().map(|folder| {
        WalkDirGeneric::<((), ())>::new(&folder)
            .skip_hidden(true)
            .sort(false)
            .into_iter()
            .filter_map(Result::ok)
            .filter(|e| e.file_type().is_file())
            .filter_map(|e| {
                let path = e.path();
                let ext_str = path.extension()
                    .map(|ext| format!(".{}", ext.to_string_lossy().to_lowercase()))
                    .unwrap_or_else(|| "".to_string());
                
                if ext_set.is_empty() || ext_set.contains(&ext_str) {
                    if let Ok(abs_path) = std::fs::canonicalize(&path) {
                        let path_str = abs_path.to_string_lossy();
                        Some(path_str.trim_start_matches(r"\\?\").to_string())
                    } else {
                        path.to_str().map(|s| s.to_string())
                    }
                } else {
                    None
                }
            })
            .collect()
    }).collect();
    
    let flat_results: Vec<String> = results.into_iter().flatten().collect();
    Ok(flat_results)
}

/// Generate a tightly packed little-endian binary buffer encoding per-item 3D
/// coordinates, a normalized size, and a 32-bit type hash.
///
/// Each item is encoded as 20 bytes in the following order:
/// - `x: f32` (4 bytes, little-endian)
/// - `y: f32` (4 bytes, little-endian)
/// - `z: f32` (4 bytes, little-endian)
/// - `norm_size: f32` (4 bytes, little-endian) — computed as `max(log10(size + 1), 0.5)`
/// - `type_hash: u32` (4 bytes, little-endian) — lower 32 bits of a `DefaultHasher` hash of the file path
///
/// The function procedurally places items using their index to derive `x`, `y`, and `z`.
///
/// # Examples
///
/// ```
/// # use pyo3::prelude::*;
/// # // example usage in Rust unit-style form; in the library this is exposed to Python
/// let files = vec![("a.txt".to_string(), 100.0_f32, "txt".to_string())];
/// let buf = get_spatial_binary(files).unwrap();
/// // one item => 20 bytes
/// assert_eq!(buf.len(), 20);
/// ```
#[pyfunction]
fn get_spatial_binary(files: Vec<(String, f32, String)>) -> PyResult<Vec<u8>> {
    let mut buffer = Vec::with_capacity(files.len() * 20);
    
    for (i, (path, size, ext)) in files.into_iter().enumerate() {
        // Procedural layout logic mirroring frontend but native
        let angle = (i as f32) * 0.1;
        let radius = 10.0 + (i as f32).sqrt() * 2.0;
        let x = angle.cos() * radius;
        let y = angle.sin() * radius;
        let z = (i as f32 % 100.0) - 50.0;
        
        let norm_size = (size + 1.0).log10().max(0.5);
        
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        use std::hash::Hasher;
        use std::hash::Hash;
        path.hash(&mut hasher);
        let type_hash = (hasher.finish() & 0xFFFFFFFF) as u32;

        buffer.write_f32::<LittleEndian>(x).unwrap();
        buffer.write_f32::<LittleEndian>(y).unwrap();
        buffer.write_f32::<LittleEndian>(z).unwrap();
        buffer.write_f32::<LittleEndian>(norm_size).unwrap();
        buffer.write_u32::<LittleEndian>(type_hash).unwrap();
    }
    
    Ok(buffer)
}

/// Computes the SHA-256 digest of a file's contents and returns it as a lowercase hex string.
///
/// If the file cannot be opened or an I/O error occurs while reading, an empty string is returned.
///
/// # Examples
///
/// ```
/// // Nonexistent file yields an empty string
/// let empty = calculate_sha256("no_such_file.txt").unwrap();
/// assert_eq!(empty, "");
/// ```
#[pyfunction]
fn calculate_sha256(path: &str) -> PyResult<String> {
    let mut file = match File::open(path) {
        Ok(f) => f,
        Err(_) => return Ok("".to_string()),
    };
    
    let mut context = Context::new(&SHA256);
    let mut buffer = [0; 1048576]; // 1MB buffer
    
    loop {
        match file.read(&mut buffer) {
            Ok(0) => break,
            Ok(n) => context.update(&buffer[..n]),
            Err(_) => return Ok("".to_string()),
        }
    }
    
    let digest = context.finish();
    Ok(hex::encode(digest.as_ref()))
}

/// Registers the `rust_core` Python module and its exported functions.
///
/// This function initializes the Python module `rust_core` with the following callable exports:
/// `find_sentence_boundary`, `create_chunks`, `scan_folders`, `get_spatial_binary`, and `calculate_sha256`.
///
/// # Examples
///
/// ```
/// use pyo3::prelude::*;
///
/// Python::with_gil(|py| {
///     // Create an empty module and register the functions onto it.
///     let m = PyModule::new(py, "rust_core").unwrap();
///     rust_core(py, m).unwrap();
///     // The module `m` now exposes the registered functions to Python code.
///
///     // Optionally, insert into sys.modules for importability from Python:
///     let sys_modules = py.import("sys").unwrap().getattr("modules").unwrap();
///     sys_modules.set_item("rust_core", m).unwrap();
/// });
/// ```
#[pymodule]
fn rust_core(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(find_sentence_boundary, m)?)?;
    m.add_function(wrap_pyfunction!(create_chunks, m)?)?;
    m.add_function(wrap_pyfunction!(scan_folders, m)?)?;
    m.add_function(wrap_pyfunction!(get_spatial_binary, m)?)?;
    m.add_function(wrap_pyfunction!(calculate_sha256, m)?)?;
    Ok(())
}
