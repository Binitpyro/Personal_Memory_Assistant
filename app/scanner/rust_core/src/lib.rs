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
    let ext_set: HashSet<String> = extensions
        .into_iter()
        .map(|e| {
            let lower = e.to_lowercase();
            if lower.starts_with('.') || lower.is_empty() {
                lower
            } else {
                format!(".{}", lower)
            }
        })
        .collect();
    
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

mod layout;
use layout::{Node, LayoutConfig, simulate_layout};
use std::collections::VecDeque;
use std::collections::HashMap;

/// Generates a tightly packed binary buffer for 3D visualization.
/// Format: Array of Node (position, radius, parent_index, flags, type_hash, pad) - 32 bytes each
#[pyfunction]
fn get_spatial_binary(files: Vec<(String, f32, String)>) -> PyResult<Vec<u8>> {
    struct TreeNode {
        size: f32,
        is_folder: bool,
        type_hash: u32,
        children: Vec<usize>,
        parent: u32,
    }
    
    let mut nodes = Vec::new();
    let mut path_to_idx = HashMap::new();

    nodes.push(TreeNode {
        size: 0.0,
        is_folder: true,
        type_hash: 0,
        children: vec![],
        parent: u32::MAX,
    });
    path_to_idx.insert("".to_string(), 0);

    for (path, size, _ext) in files {
        let path = path.replace("\\", "/");
        let parts: Vec<&str> = path.split('/').collect();
        let mut current_path = String::new();
        let mut parent_idx = 0;
        
        for (i, part) in parts.iter().enumerate() {
            if part.is_empty() { continue; }
            
            let is_last = i == parts.len() - 1;
            let p = if current_path.is_empty() { part.to_string() } else { format!("{}/{}", current_path, part) };
            
            let idx = *path_to_idx.entry(p.clone()).or_insert_with(|| {
                let new_idx = nodes.len();
                nodes[parent_idx].children.push(new_idx);
                
                let mut hasher = std::collections::hash_map::DefaultHasher::new();
                use std::hash::{Hash, Hasher};
                p.hash(&mut hasher);
                let type_hash = (hasher.finish() & 0xFFFFFFFF) as u32;

                nodes.push(TreeNode {
                    size: if is_last { size } else { 0.0 }, // folders don't have direct size
                    is_folder: !is_last,
                    type_hash,
                    children: vec![],
                    parent: parent_idx as u32,
                });
                new_idx
            });
            
            parent_idx = idx;
            current_path = p;
        }
    }
    
    let mut bfs_order = Vec::new();
    let mut queue = VecDeque::new();
    queue.push_back(0);
    while let Some(idx) = queue.pop_front() {
        bfs_order.push(idx);
        for &child in &nodes[idx].children {
            queue.push_back(child);
        }
    }

    let mut new_indices = vec![0; nodes.len()];
    for (new_idx, &old_idx) in bfs_order.iter().enumerate() {
        new_indices[old_idx] = new_idx;
    }

    let mut gpu_nodes = vec![Node::default(); nodes.len()];
    for (new_idx, &old_idx) in bfs_order.iter().enumerate() {
        let old_node = &nodes[old_idx];
        let parent_idx = if old_node.parent == u32::MAX { u32::MAX } else { new_indices[old_node.parent as usize] as u32 };
        
        let pos = if parent_idx == u32::MAX {
            [0.0, 0.0, 0.0]
        } else {
            let parent_pos = gpu_nodes[parent_idx as usize].position;
            let angle = (new_idx as f32) * 2.39996; // discrete spiral angle approximation
            let r = 10.0;
            [parent_pos[0] + angle.cos() * r, parent_pos[1] + angle.sin() * r, parent_pos[2] + (new_idx as f32 % 10.0) - 5.0]
        };
        
        let radius = if old_node.is_folder { 20.0 } else { 10.0 + (old_node.size + 1.0).log10().max(0.5) * 2.0 };
        
        gpu_nodes[new_idx] = Node {
            position: pos,
            radius,
            parent_index: parent_idx,
            flags: if old_node.is_folder { 1 } else { 0 },
            type_hash: old_node.type_hash,
            pad: 0,
        };
    }
    
    let config = LayoutConfig::default();
    simulate_layout(&mut gpu_nodes, &config);

    // Stream the binary data
    let mut buffer = Vec::new();
    let slice_u8 = unsafe {
        std::slice::from_raw_parts(
            gpu_nodes.as_ptr() as *const u8,
            gpu_nodes.len() * std::mem::size_of::<Node>()
        )
    };
    buffer.extend_from_slice(slice_u8);
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
