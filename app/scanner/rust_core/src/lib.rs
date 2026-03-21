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
    
    ["\n\n", ". ", "! ", "? ", ".\n", "!\n", "?\n"]
        .iter()
        .filter_map(|&delim| region.rfind(delim).map(|idx| search_start + idx + delim.len()))
        .max()
        .unwrap_or(byte_pos)
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

use std::collections::VecDeque;
use std::collections::HashMap;

#[repr(C, align(32))]
#[derive(Clone, Copy, Debug, Default)]
pub struct Node {
    pub position: [f32; 3],
    pub radius: f32,
    pub parent_index: u32,
    pub flags: u32,
    pub type_hash: u32,
    pub pad: u32,
}

struct TreeNode {
    size: f32,
    is_folder: bool,
    type_hash: u32,
    children: Vec<usize>,
    parent: u32,
    radius: f32,
    local_pos: [f32; 3],
    global_pos: [f32; 3],
}

fn build_tree(files: Vec<(String, f32, String)>) -> Vec<TreeNode> {
    let mut nodes = Vec::new();
    let mut path_to_idx = HashMap::new();

    nodes.push(TreeNode {
        size: 0.0,
        is_folder: true,
        type_hash: 0,
        children: vec![],
        parent: u32::MAX,
        radius: 0.0,
        local_pos: [0.0; 3],
        global_pos: [0.0; 3],
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
                    size: if is_last { size } else { 0.0 },
                    is_folder: !is_last,
                    type_hash,
                    children: vec![],
                    parent: parent_idx as u32,
                    radius: 0.0,
                    local_pos: [0.0; 3],
                    global_pos: [0.0; 3],
                });
                new_idx
            });
            
            parent_idx = idx;
            current_path = p;
        }
    }
    nodes
}

/// Generates a tightly packed binary buffer for 3D visualization using Hierarchical Spherical Packing
#[pyfunction]
fn get_spatial_binary(files: Vec<(String, f32, String)>) -> PyResult<Vec<u8>> {
    let mut nodes = build_tree(files);
    
    // 1. Post-order traversal (Bottom-Up)
    let mut post_order = Vec::new();
    let mut stack = vec![0];
    while let Some(node) = stack.pop() {
        post_order.push(node);
        for &child in &nodes[node].children {
            stack.push(child);
        }
    }
    post_order.reverse();

    // 2. Pack children hierarchically to find true radii
    for &idx in &post_order {
        if !nodes[idx].is_folder {
            // Leaf size
            nodes[idx].radius = 1.0 + (nodes[idx].size + 1.0).log10().max(0.0) * 1.5;
        } else {
            let child_count = nodes[idx].children.len();
            if child_count == 0 {
                nodes[idx].radius = 4.0;
                continue;
            }

            let mut child_data: Vec<(usize, f32, [f32; 3])> = nodes[idx].children.iter()
                .map(|&c| (c, nodes[c].radius, [0.0, 0.0, 0.0]))
                .collect();
            
            // Sort by radius descending for tighter 3D packing
            child_data.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));

            // Fibonacci spiral initialization for even initial 3D spread
            let golden_ratio = (1.0 + 5.0_f32.sqrt()) / 2.0;
            let angle_increment = std::f32::consts::PI * 2.0 * golden_ratio;
            
            for (i, cd) in child_data.iter_mut().enumerate() {
                let t = i as f32 / child_count as f32;
                let inclination = (1.0 - 2.0 * t).acos();
                let azimuth = angle_increment * i as f32;
                let r = cd.1 * (i as f32).sqrt() * 0.5; // Spread based on size
                
                cd.2 = [
                    r * inclination.sin() * azimuth.cos(),
                    r * inclination.sin() * azimuth.sin(),
                    r * inclination.cos()
                ];
            }

            // Local simulation: Remove overlap and compress into a tight sphere
            for _ in 0..150 {
                // Gravity pulling to local center
                for cd in &mut child_data {
                    cd.2[0] *= 0.95; cd.2[1] *= 0.95; cd.2[2] *= 0.95;
                }
                
                // Collision resolution
                for i in 0..child_count {
                    for j in (i+1)..child_count {
                        let dx = child_data[i].2[0] - child_data[j].2[0];
                        let dy = child_data[i].2[1] - child_data[j].2[1];
                        let dz = child_data[i].2[2] - child_data[j].2[2];
                        let dist_sq = dx*dx + dy*dy + dz*dz;
                        
                        let min_dist = child_data[i].1 + child_data[j].1 + 0.8; // 0.8 padding between items
                        
                        if dist_sq < min_dist * min_dist && dist_sq > 0.0001 {
                            let dist = dist_sq.sqrt();
                            let overlap = min_dist - dist;
                            let nx = dx / dist; let ny = dy / dist; let nz = dz / dist;
                            
                            let total_r = child_data[i].1 + child_data[j].1;
                            let ratio_i = child_data[j].1 / total_r;
                            let ratio_j = child_data[i].1 / total_r;

                            let push = overlap * 0.5;
                            child_data[i].2[0] += nx * push * ratio_i;
                            child_data[i].2[1] += ny * push * ratio_i;
                            child_data[i].2[2] += nz * push * ratio_i;
                            
                            child_data[j].2[0] -= nx * push * ratio_j;
                            child_data[j].2[1] -= ny * push * ratio_j;
                            child_data[j].2[2] -= nz * push * ratio_j;
                        }
                    }
                }
            }

            // Folder radius is the bounding sphere of its packed children
            let mut bounding_radius = 0.0_f32;
            for cd in &child_data {
                let dist = (cd.2[0].powi(2) + cd.2[1].powi(2) + cd.2[2].powi(2)).sqrt();
                if dist + cd.1 > bounding_radius {
                    bounding_radius = dist + cd.1;
                }
                nodes[cd.0].local_pos = cd.2;
            }
            nodes[idx].radius = bounding_radius + 2.0; // Crystal shell thickness
        }
    }

    // 3. Top-Down pass to compute absolute global coordinates
    nodes[0].global_pos = [0.0, 0.0, 0.0];
    let mut top_down_queue = VecDeque::new();
    top_down_queue.push_back(0);
    while let Some(idx) = top_down_queue.pop_front() {
        let parent_pos = nodes[idx].global_pos;
        
        let children = nodes[idx].children.clone(); 
        
        for c_idx in children {
            let local = nodes[c_idx].local_pos;
            nodes[c_idx].global_pos = [
                parent_pos[0] + local[0],
                parent_pos[1] + local[1],
                parent_pos[2] + local[2],
            ];
            top_down_queue.push_back(c_idx);
        }
    }

    // 4. Map to binary buffer (BFS order for cache locality)
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
        
        gpu_nodes[new_idx] = Node {
            position: old_node.global_pos,
            radius: old_node.radius,
            parent_index: parent_idx,
            flags: if old_node.is_folder { 1 } else { 0 },
            type_hash: old_node.type_hash,
            pad: 0,
        };
    }
    
    // Note: We completely remove the call to `simulate_layout` here! 

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

/// Extracts text from multiple files concurrently, removing BOM.
/// Detects binary files and replaces them with a stub message.
#[pyfunction]
fn extract_text_files(paths: Vec<String>, max_size: usize) -> PyResult<Vec<(String, String)>> {
    let results: Vec<(String, String)> = paths.into_par_iter()
        .map(|path| {
            let fallback_stub = format!("[UNREADABLE: {}]", path);
            match File::open(&path) {
                Ok(mut file) => {
                    let mut buffer = Vec::new();
                    match file.by_ref().take(max_size as u64 + 1).read_to_end(&mut buffer) {
                        Ok(n) => {
                            if n > max_size {
                                buffer.truncate(max_size);
                            }
                            
                            // Check binary heuristic (similar to Python logic)
                            let sample_len = std::cmp::min(8192, buffer.len());
                            let mut non_text = 0;
                            let mut has_null = false;
                            for &b in &buffer[..sample_len] {
                                if b == 0 { has_null = true; break; }
                                if b < 32 && b != 9 && b != 10 && b != 13 { non_text += 1; }
                            }
                            if has_null || (sample_len > 0 && (non_text as f32 / sample_len as f32 > 0.3)) {
                                return (path.clone(), format!("[BINARY: {}] Binary content not indexed.", path));
                            }
                            
                            let text = String::from_utf8_lossy(&buffer);
                            let clean_text = if text.starts_with('\u{feff}') {
                                text.chars().skip(1).collect()
                            } else {
                                text.into_owned()
                            };
                            (path, clean_text)
                        }
                        Err(_) => (path, fallback_stub),
                    }
                }
                Err(_) => (path, fallback_stub),
            }
        })
        .collect();
    Ok(results)
}

/// A Python module implemented in Rust using PyO3.
#[pymodule]
fn rust_core(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(find_sentence_boundary, m)?)?;
    m.add_function(wrap_pyfunction!(create_chunks, m)?)?;
    m.add_function(wrap_pyfunction!(scan_folders, m)?)?;
    m.add_function(wrap_pyfunction!(get_spatial_binary, m)?)?;
    m.add_function(wrap_pyfunction!(calculate_sha256, m)?)?;
    m.add_function(wrap_pyfunction!(extract_text_files, m)?)?;
    Ok(())
}
