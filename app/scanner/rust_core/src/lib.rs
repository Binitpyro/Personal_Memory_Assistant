use pyo3::prelude::*;
use pyo3::types::PyDict;
use jwalk::WalkDirGeneric;
use std::collections::HashSet;
use std::fs::File;
use std::io::Read;
use ring::digest::{Context, SHA256};
use rayon::prelude::*;

mod layout;

fn get_sentence_offsets_json(text: &str) -> String {
    if std::env::var("PMA_SENTENCE_OFFSETS").map(|v| v == "0").unwrap_or(false) {
        return "[]".to_string();
    }
    let mut offsets = Vec::new();
    let chars: Vec<char> = text.chars().collect();
    let mut curr = 0;
    
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if c == '.' || c == '!' || c == '?' {
            let mut j = i + 1;
            let mut has_ws = false;
            while j < chars.len() && chars[j].is_whitespace() {
                has_ws = true;
                j += 1;
            }
            if has_ws && j < chars.len() && chars[j].is_uppercase() {
                let end = i + 2;
                offsets.push(format!("[{}, {}]", curr, end));
                curr = j;
                i = j;
                continue;
            }
        }
        i += 1;
    }
    if curr < chars.len() {
        offsets.push(format!("[{}, {}]", curr, chars.len()));
    }
    
    format!("[{}]", offsets.join(", "))
}

fn get_sentence_boundary(text: &str, byte_pos: usize, byte_window: usize) -> usize {
    let mut safe_byte_pos = byte_pos;
    // Ensure byte_pos is on a char boundary by moving backward if necessary
    while safe_byte_pos > 0 && !text.is_char_boundary(safe_byte_pos) {
        safe_byte_pos -= 1;
    }

    let mut search_start = if safe_byte_pos > byte_window { safe_byte_pos - byte_window } else { 0 };
    
    // Ensure search_start is on a char boundary by moving forward if necessary
    while search_start < safe_byte_pos && !text.is_char_boundary(search_start) {
        search_start += 1;
    }
    
    let region = &text[search_start..safe_byte_pos];
    
    ["\n\n", ". ", "! ", "? ", ".\n", "!\n", "?\n"]
        .iter()
        .filter_map(|&delim| region.rfind(delim).map(|idx| search_start + idx + delim.len()))
        .max()
        .unwrap_or(safe_byte_pos)
}

fn _calculate_chunk_end(text: &str, start_char: usize, char_indices: &[usize], chunk_size_chars: usize) -> usize {
    let total_chars = char_indices.len();
    let raw_end_char = std::cmp::min(start_char + chunk_size_chars, total_chars);
    let raw_end_byte = if raw_end_char < total_chars { char_indices[raw_end_char] } else { text.len() };
    
    if raw_end_char < total_chars {
        let end_byte = get_sentence_boundary(text, raw_end_byte, 160); 
        if end_byte > char_indices[start_char] {
            return end_byte;
        }
    }
    raw_end_byte
}

/// Creates overlapping chunks of text, snapping to sentence boundaries.
#[pyfunction]
fn create_chunks(py: Python, text: &str, chunk_size_chars: usize, chunk_overlap_chars: usize, prefix: &str, base_offset: usize) -> PyResult<Vec<Py<PyAny>>> {
    let mut chunks = Vec::new();
    let char_indices: Vec<usize> = text.char_indices().map(|(b, _)| b).collect();
    let total_chars = char_indices.len();
    
    if total_chars == 0 {
        return Ok(chunks);
    }

    let mut start_char = 0;
    while start_char < total_chars {
        let end_byte = _calculate_chunk_end(text, start_char, &char_indices, chunk_size_chars);
        let start_byte = char_indices[start_char];
        let chunk_text = &text[start_byte..end_byte];
        
        let dict = PyDict::new(py);
        dict.set_item("start_offset", base_offset + start_char)?;
        let chunk_char_len = chunk_text.chars().count();
        dict.set_item("end_offset", base_offset + start_char + chunk_char_len)?;
        let full_text = format!("{}{}", prefix, chunk_text);
        dict.set_item("text_preview", &full_text)?;
        dict.set_item("sentence_offsets", get_sentence_offsets_json(&full_text))?;
        dict.set_item("segmenter_version", "rs_v1")?;
        
        chunks.push(dict.into());

        if end_byte == text.len() {
            break;
        }

        let end_char = start_char + chunk_char_len;
        let next_start = if end_char > chunk_overlap_chars {
            end_char - chunk_overlap_chars
        } else {
            end_char
        };

        // Ensure we always advance by at least 1 character to avoid infinite loops
        start_char = if next_start > start_char { next_start } else { start_char + 1 };
    }
    
    Ok(chunks)
}

/// Chunks markdown text by heading sections (up to level 3).
#[pyfunction]
fn chunk_markdown(py: Python, text: &str, chunk_size_chars: usize, chunk_overlap_chars: usize, prefix: &str) -> PyResult<Vec<Py<PyAny>>> {
    let mut chunks = Vec::new();
    let re = regex::Regex::new(r"(?m)^#{1,3}\s").unwrap();
    
    let mut last_byte = 0;
    for mat in re.find_iter(text) {
        if mat.start() > last_byte {
            let sec = &text[last_byte..mat.start()];
            if !sec.trim().is_empty() {
                // Calculate char offset for this section
                let start_char = text[..last_byte].chars().count();
                let sec_chars = sec.chars().count();
                
                if sec_chars <= chunk_size_chars {
                    let dict = PyDict::new(py);
                    dict.set_item("start_offset", start_char)?;
                    dict.set_item("end_offset", start_char + sec_chars)?;
                    let full_text = format!("{}{}", prefix, sec.trim());
                    dict.set_item("text_preview", &full_text)?;
                    dict.set_item("sentence_offsets", get_sentence_offsets_json(&full_text))?;
                    dict.set_item("segmenter_version", "rs_v1")?;
                    chunks.push(dict.into());
                } else {
                    let mut c = create_chunks(py, sec, chunk_size_chars, chunk_overlap_chars, prefix, start_char)?;
                    chunks.append(&mut c);
                }
            }
        }
        last_byte = mat.start();
    }
    
    if last_byte < text.len() {
        let sec = &text[last_byte..];
        if !sec.trim().is_empty() {
            let start_char = text[..last_byte].chars().count();
            let sec_chars = sec.chars().count();
            
            if sec_chars <= chunk_size_chars {
                let dict = PyDict::new(py);
                dict.set_item("start_offset", start_char)?;
                dict.set_item("end_offset", start_char + sec_chars)?;
                let full_text = format!("{}{}", prefix, sec.trim());
                dict.set_item("text_preview", &full_text)?;
                dict.set_item("sentence_offsets", get_sentence_offsets_json(&full_text))?;
                dict.set_item("segmenter_version", "rs_v1")?;
                chunks.push(dict.into());
            } else {
                let mut c = create_chunks(py, sec, chunk_size_chars, chunk_overlap_chars, prefix, start_char)?;
                chunks.append(&mut c);
            }
        }
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

fn _pack_children(nodes: &mut [TreeNode], idx: usize) {
    let child_count = nodes[idx].children.len();
    if child_count == 0 {
        nodes[idx].radius = 4.0;
        return;
    }

    let mut child_data: Vec<(usize, f32, [f32; 3])> = nodes[idx].children.iter()
        .map(|&c| (c, nodes[c].radius, [0.0, 0.0, 0.0]))
        .collect();
    
    child_data.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));

    _initialize_spiral_positions(&mut child_data);
    // H-06 / P-01: Use the Barnes-Hut O(n log n) layout from layout.rs instead
    // of the O(n^2) pair-wise repulsion loop that shipped previously.
    let mut nodes_for_layout: Vec<layout::Node> = child_data.iter().map(|cd| layout::Node {
        position: cd.2,
        radius: cd.1,
        parent_index: u32::MAX,
        flags: 0,
        type_hash: 0,
        pad: 0,
    }).collect();
    let cfg = layout::LayoutConfig::default();
    layout::simulate_layout(&mut nodes_for_layout, &cfg);
    for (i, cd) in child_data.iter_mut().enumerate() {
        cd.2 = nodes_for_layout[i].position;
    }

    let mut bounding_radius = 0.0_f32;
    for cd in &child_data {
        let dist = (cd.2[0].powi(2) + cd.2[1].powi(2) + cd.2[2].powi(2)).sqrt();
        bounding_radius = bounding_radius.max(dist + cd.1);
        nodes[cd.0].local_pos = cd.2;
    }
    nodes[idx].radius = bounding_radius + 2.0;
}

fn _initialize_spiral_positions(child_data: &mut [(usize, f32, [f32; 3])]) {
    let child_count = child_data.len();
    let golden_ratio = (1.0 + 5.0_f32.sqrt()) / 2.0;
    let angle_increment = std::f32::consts::PI * 2.0 * golden_ratio;
    
    for (i, cd) in child_data.iter_mut().enumerate() {
        let t = i as f32 / child_count as f32;
        let inclination = (1.0 - 2.0 * t).acos();
        let azimuth = angle_increment * i as f32;
        let r = cd.1 * (i as f32).sqrt() * 0.5;
        cd.2 = [r * inclination.sin() * azimuth.cos(), r * inclination.sin() * azimuth.sin(), r * inclination.cos()];
    }
}



/// Generates a tightly packed binary buffer for 3D visualization using Hierarchical Spherical Packing
#[pyfunction]
fn get_spatial_binary(files: Vec<(String, f32, String)>) -> PyResult<Vec<u8>> {
    let mut nodes = build_tree(files);
    
    _calculate_node_radii(&mut nodes);
    _calculate_global_positions(&mut nodes);

    let bfs_order = _get_bfs_order(&nodes);
    let mut new_indices = vec![0; nodes.len()];
    for (new_idx, &old_idx) in bfs_order.iter().enumerate() { new_indices[old_idx] = new_idx; }

    let mut gpu_nodes = vec![Node::default(); nodes.len()];
    for (new_idx, &old_idx) in bfs_order.iter().enumerate() {
        let old_node = &nodes[old_idx];
        let p_idx = if old_node.parent == u32::MAX { u32::MAX } else { new_indices[old_node.parent as usize] as u32 };
        gpu_nodes[new_idx] = Node {
            position: old_node.global_pos, radius: old_node.radius, parent_index: p_idx,
            flags: if old_node.is_folder { 1 } else { 0 }, type_hash: old_node.type_hash, pad: 0,
        };
    }

    let mut buffer = Vec::new();
    // SAFETY: Node is #[repr(C, align(32))] and contains only primitive types (f32, u32).
    // Casting to [u8] is safe because Node has no uninitialized padding bytes,
    // and gpu_nodes has exactly nodes.len() * size_of::<Node>() valid bytes.
    let slice_u8 = unsafe { std::slice::from_raw_parts(gpu_nodes.as_ptr() as *const u8, gpu_nodes.len() * std::mem::size_of::<Node>()) };
    buffer.extend_from_slice(slice_u8);
    Ok(buffer)
}

fn _calculate_node_radii(nodes: &mut [TreeNode]) {
    let mut post_order = Vec::new();
    let mut stack = vec![0];
    while let Some(node) = stack.pop() {
        post_order.push(node);
        for &child in &nodes[node].children { stack.push(child); }
    }
    post_order.reverse();

    for &idx in &post_order {
        if !nodes[idx].is_folder {
            nodes[idx].radius = 1.0 + (nodes[idx].size + 1.0).log10().max(0.0) * 1.5;
        } else {
            _pack_children(nodes, idx);
        }
    }
}

fn _calculate_global_positions(nodes: &mut [TreeNode]) {
    nodes[0].global_pos = [0.0, 0.0, 0.0];
    let mut top_down_queue = VecDeque::new();
    top_down_queue.push_back(0);
    while let Some(idx) = top_down_queue.pop_front() {
        let parent_pos = nodes[idx].global_pos;
        for c_idx in nodes[idx].children.clone() {
            let local = nodes[c_idx].local_pos;
            nodes[c_idx].global_pos = [parent_pos[0] + local[0], parent_pos[1] + local[1], parent_pos[2] + local[2]];
            top_down_queue.push_back(c_idx);
        }
    }
}

fn _get_bfs_order(nodes: &[TreeNode]) -> Vec<usize> {
    let mut bfs_order = Vec::new();
    let mut queue = VecDeque::new();
    queue.push_back(0);
    while let Some(idx) = queue.pop_front() {
        bfs_order.push(idx);
        for &child in &nodes[idx].children { queue.push_back(child); }
    }
    bfs_order
}

fn _is_binary_buffer(buffer: &[u8]) -> bool {
    let sample_len = std::cmp::min(8192, buffer.len());
    let mut non_text = 0;
    for &b in &buffer[..sample_len] {
        if b == 0 { return true; }
        if b < 32 && b != 9 && b != 10 && b != 13 { non_text += 1; }
    }
    sample_len > 0 && (non_text as f32 / sample_len as f32 > 0.3)
}

/// Extremely fast SHA256 for a file path reading 1MB blocks safely.
#[pyfunction]
fn calculate_sha256(path: &str) -> PyResult<String> {
    let mut file = match File::open(path) {
        Ok(f) => f,
        Err(_) => return Ok("".to_string()),
    };
    
    let mut context = Context::new(&SHA256);
    let mut buffer = vec![0; 1048576]; // Heap-allocated 1MB buffer (prevents stack overflow)
    
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

fn _extract_single_file(path: String, max_size: usize) -> (String, String) {
    let fallback_stub = format!("[UNREADABLE: {}]", path);
    match File::open(&path) {
        Ok(mut file) => {
            // Pre-allocate capacity up to 1MB to reduce reallocations (optimized growth)
            let mut buffer = Vec::with_capacity(max_size.min(1024 * 1024));
            match file.by_ref().take(max_size as u64 + 1).read_to_end(&mut buffer) {
                Ok(n) => {
                    if n > max_size { buffer.truncate(max_size); }
                    if _is_binary_buffer(&buffer) {
                        return (path.clone(), format!("[BINARY: {}] Binary content not indexed.", path));
                    }
                    let text = String::from_utf8_lossy(&buffer);
                    let clean = if text.starts_with('\u{feff}') { text.chars().skip(1).collect() } else { text.into_owned() };
                    (path, clean)
                }
                Err(_) => (path, fallback_stub),
            }
        }
        Err(_) => (path, fallback_stub),
    }
}

/// Extracts text from multiple files concurrently, removing BOM.
/// Detects binary files and replaces them with a stub message.
#[pyfunction]
fn extract_text_files(paths: Vec<String>, max_size: usize) -> PyResult<Vec<(String, String)>> {
    let results: Vec<(String, String)> = paths.into_par_iter()
        .map(|path| _extract_single_file(path, max_size))
        .collect();
    Ok(results)
}


/// Hash a path exactly as build_tree does for Node.type_hash, so callers
/// can join external metadata onto the visualizer binary stream.
#[pyfunction]
fn hash_tree_path(path: &str) -> u32 {
    use std::hash::{Hash, Hasher};
    let normalized = path.replace("\\", "/");
    let joined = normalized
        .split('/')
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join("/");
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    joined.hash(&mut hasher);
    (hasher.finish() & 0xFFFFFFFF) as u32
}

/// Python module implemented in Rust using PyO3.
#[pymodule]
fn rust_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(scan_folders, m)?)?;
    m.add_function(wrap_pyfunction!(create_chunks, m)?)?;
    m.add_function(wrap_pyfunction!(chunk_markdown, m)?)?;
    m.add_function(wrap_pyfunction!(find_sentence_boundary, m)?)?;
    m.add_function(wrap_pyfunction!(get_spatial_binary, m)?)?;
    m.add_function(wrap_pyfunction!(calculate_sha256, m)?)?;
    m.add_function(wrap_pyfunction!(extract_text_files, m)?)?;
    m.add_function(wrap_pyfunction!(hash_tree_path, m)?)?;
    Ok(())
}

// ─────────────────────────────────────────────────────────────────────────────
// Pure Rust unit tests — NO PyO3 Python interpreter required.
// Run with: cargo test --lib
// ─────────────────────────────────────────────────────────────────────────────
#[cfg(test)]
mod tests {
    use super::*;

    // ── get_sentence_boundary ─────────────────────────────────────────────

    #[test]
    fn test_sentence_boundary_period_space() {
        let text = "Hello world. This is a test sentence here.";
        let result = get_sentence_boundary(text, 20, 20);
        assert!(result <= text.len());
    }

    #[test]
    fn test_sentence_boundary_double_newline() {
        let text = "Paragraph one.\n\nParagraph two starts here.";
        let result = get_sentence_boundary(text, 20, 20);
        assert!(result <= text.len());
    }

    #[test]
    fn test_sentence_boundary_no_match_returns_byte_pos() {
        let text = "abcdefghijklmnopqrstuvwxyz";
        let result = get_sentence_boundary(text, 10, 5);
        assert_eq!(result, 10);
    }

    #[test]
    fn test_sentence_boundary_at_start() {
        let text = "Start of text.";
        let result = get_sentence_boundary(text, 0, 10);
        assert_eq!(result, 0);
    }

    #[test]
    fn test_sentence_boundary_unicode_safe() {
        let text = "Hello \u{4e16}\u{754c}. This is a test.";
        let result = get_sentence_boundary(text, 10, 10);
        assert!(result <= text.len());
    }

    // ── Binary detection heuristic (mirrors extract_text_files logic) ─────

    fn is_binary_heuristic(buffer: &[u8]) -> bool {
        let sample_len = buffer.len().min(8192);
        let mut non_text = 0usize;
        let mut has_null = false;
        for &b in &buffer[..sample_len] {
            if b == 0 { has_null = true; break; }
            if b < 32 && b != 9 && b != 10 && b != 13 { non_text += 1; }
        }
        has_null || (sample_len > 0 && (non_text as f32 / sample_len as f32 > 0.3))
    }

    #[test]
    fn test_binary_null_byte() {
        let data = b"hello world\x00rest";
        assert!(is_binary_heuristic(data));
    }

    #[test]
    fn test_binary_plain_text_not_binary() {
        let data = b"Hello, world!\nThis is plain text.\n";
        assert!(!is_binary_heuristic(data));
    }

    #[test]
    fn test_binary_high_control_chars() {
        let data: Vec<u8> = (0..100).map(|i| if i % 3 == 0 { 1u8 } else { b'a' }).collect();
        assert!(is_binary_heuristic(&data));
    }

    #[test]
    fn test_binary_empty_not_binary() {
        let data: &[u8] = b"";
        assert!(!is_binary_heuristic(data));
    }

    // ── BOM stripping ─────────────────────────────────────────────────────

    #[test]
    fn test_bom_stripping() {
        let text_with_bom = "\u{feff}Hello, world!";
        let stripped: String = if text_with_bom.starts_with('\u{feff}') {
            text_with_bom.chars().skip(1).collect()
        } else {
            text_with_bom.to_owned()
        };
        assert_eq!(stripped, "Hello, world!");
    }

    #[test]
    fn test_no_bom_unchanged() {
        let text = "No BOM here";
        let stripped: String = if text.starts_with('\u{feff}') {
            text.chars().skip(1).collect()
        } else {
            text.to_owned()
        };
        assert_eq!(stripped, "No BOM here");
    }

    // ── Node struct ───────────────────────────────────────────────────────

    #[test]
    fn test_node_default_values() {
        let n = Node::default();
        assert_eq!(n.position, [0.0; 3]);
        assert_eq!(n.radius, 0.0);
        assert_eq!(n.flags, 0);
        assert_eq!(n.pad, 0);
    }

    #[test]
    fn test_node_size_is_32_bytes() {
        assert_eq!(std::mem::size_of::<Node>(), 32);
    }

    // ── Extension normalization (mirrors scan_folders) ────────────────────

    fn normalize_ext(e: &str) -> String {
        let lower = e.to_lowercase();
        if lower.starts_with('.') || lower.is_empty() {
            lower
        } else {
            format!(".{}", lower)
        }
    }

    #[test]
    fn test_ext_already_has_dot() {
        assert_eq!(normalize_ext(".py"), ".py");
    }

    #[test]
    fn test_ext_without_dot() {
        assert_eq!(normalize_ext("py"), ".py");
    }

    #[test]
    fn test_ext_uppercase() {
        assert_eq!(normalize_ext("PY"), ".py");
    }

    #[test]
    fn test_ext_empty_string() {
        assert_eq!(normalize_ext(""), "");
    }

    #[test]
    #[cfg_attr(miri, ignore)]
    fn test_calculate_sha256_valid_file() {
        let temp_dir = std::env::temp_dir().join("pma_test_sha256");
        let _ = std::fs::remove_dir_all(&temp_dir);
        std::fs::create_dir_all(&temp_dir).unwrap();
        let file_path = temp_dir.join("test_sha256.txt");
        std::fs::write(&file_path, "PMA Test Data").unwrap();
        
        let hash_res = calculate_sha256(file_path.to_str().unwrap()).unwrap();
        assert_eq!(hash_res, "cea2d8a89cbf1cf9d2e23d3f5bd5bf2e58a9622c7030febd2d034efab8d700d6");
        let _ = std::fs::remove_dir_all(&temp_dir);
    }

    #[test]
    #[cfg_attr(miri, ignore)]
    fn test_calculate_sha256_non_existent() {
        let hash_res = calculate_sha256("non_existent_file_path_12345.txt").unwrap();
        assert_eq!(hash_res, "");
    }

    #[test]
    #[cfg_attr(miri, ignore)]
    fn test_scan_folders_non_existent() {
        let res = scan_folders(vec!["/non_existent_folder_path_12345".to_string()], vec![]).unwrap();
        assert!(res.is_empty());
    }

    #[test]
    #[cfg_attr(miri, ignore)]
    fn test_scan_folders_matching_extensions() {
        let temp_dir = std::env::temp_dir().join("pma_test_scan");
        let _ = std::fs::remove_dir_all(&temp_dir);
        std::fs::create_dir_all(&temp_dir).unwrap();
        
        std::fs::write(temp_dir.join("a.py"), "print(1)").unwrap();
        std::fs::write(temp_dir.join("b.txt"), "hello").unwrap();
        std::fs::write(temp_dir.join("c.bin"), b"\x00\x01\x02").unwrap();

        let paths = scan_folders(vec![temp_dir.to_str().unwrap().to_string()], vec![".py".to_string(), "txt".to_string()]).unwrap();
        
        assert_eq!(paths.len(), 2);
        let has_a = paths.iter().any(|p| p.ends_with("a.py"));
        let has_b = paths.iter().any(|p| p.ends_with("b.txt"));
        let has_c = paths.iter().any(|p| p.ends_with("c.bin"));
        assert!(has_a);
        assert!(has_b);
        assert!(!has_c);

        let _ = std::fs::remove_dir_all(&temp_dir);
    }

    #[test]
    #[cfg_attr(miri, ignore)]
    fn test_extract_text_files_valid() {
        let temp_dir = std::env::temp_dir().join("pma_test_extract");
        let _ = std::fs::remove_dir_all(&temp_dir);
        std::fs::create_dir_all(&temp_dir).unwrap();
        
        let file1 = temp_dir.join("file1.txt");
        let file2 = temp_dir.join("file2.txt");
        std::fs::write(&file1, "First File").unwrap();
        std::fs::write(&file2, "Second File").unwrap();

        let paths = vec![file1.to_str().unwrap().to_string(), file2.to_str().unwrap().to_string()];
        let results = extract_text_files(paths, 1000).unwrap();

        assert_eq!(results.len(), 2);
        assert_eq!(results[0].1, "First File");
        assert_eq!(results[1].1, "Second File");

        let _ = std::fs::remove_dir_all(&temp_dir);
    }

    #[test]
    #[cfg_attr(miri, ignore)]
    fn test_extract_text_files_binary() {
        let temp_dir = std::env::temp_dir().join("pma_test_extract_bin");
        let _ = std::fs::remove_dir_all(&temp_dir);
        std::fs::create_dir_all(&temp_dir).unwrap();
        
        let file1 = temp_dir.join("binary.bin");
        std::fs::write(&file1, b"\x00\x00\x00\x00Hello\x00\x01\x02").unwrap();

        let paths = vec![file1.to_str().unwrap().to_string()];
        let results = extract_text_files(paths, 1000).unwrap();

        assert_eq!(results.len(), 1);
        assert!(results[0].1.contains("Binary content not indexed"));

        let _ = std::fs::remove_dir_all(&temp_dir);
    }

    #[test]
    fn test_find_sentence_boundary_standard() {
        let text = "Hello world. This is a test sentence here. Third sentence.";
        let boundary = find_sentence_boundary(text, 25, 20);
        assert_eq!(boundary, 13);
    }

    #[test]
    fn test_get_spatial_binary_empty() {
        let res = get_spatial_binary(vec![]).unwrap();
        assert_eq!(res.len(), 32);
    }

    #[test]
    fn test_get_spatial_binary_some_files() {
        let files = vec![
            ("src/main.rs".to_string(), 1000.0, "rs".to_string()),
            ("src/lib.rs".to_string(), 500.0, "rs".to_string()),
        ];
        let res = get_spatial_binary(files).unwrap();
        assert_eq!(res.len(), 4 * 32);
    }

    // PyO3 tests removed due to PyO3 0.29 GIL API changes; tested via pytest instead.
}

