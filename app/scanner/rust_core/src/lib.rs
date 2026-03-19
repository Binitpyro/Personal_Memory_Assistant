use byteorder::{LittleEndian, WriteBytesExt};
use pyo3::prelude::*;
use pyo3::types::PyDict;
use jwalk::WalkDirGeneric;
use std::collections::HashSet;
use std::fs::File;
use std::io::Read;
use ring::digest::{Context, SHA256};
use rayon::prelude::*;

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

/// Creates overlapping chunks of text, snapping to sentence boundaries.
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

/// Finds the nearest sentence-ending punctuation near `char_pos`.
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

/// Fast parallel directory scanner returning a list of valid file paths.
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

/// Generates a tightly packed binary buffer for 3D visualization.
/// Format: [node1_x, node1_y, node1_z, node1_size, node1_typehash]...
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

/// Extremely fast SHA256 for a file path reading 1MB blocks safely.
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

/// A Python module implemented in Rust using PyO3.
#[pymodule]
fn rust_core(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(find_sentence_boundary, m)?)?;
    m.add_function(wrap_pyfunction!(create_chunks, m)?)?;
    m.add_function(wrap_pyfunction!(scan_folders, m)?)?;
    m.add_function(wrap_pyfunction!(get_spatial_binary, m)?)?;
    m.add_function(wrap_pyfunction!(calculate_sha256, m)?)?;
    Ok(())
}
