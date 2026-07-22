use rayon::prelude::*;

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

pub struct LayoutConfig {
    pub iterations: usize,
    pub repulsion_strength: f32,
    pub spring_stiffness: f32,
    pub damping: f32,
    pub theta: f32, // for Barnes-Hut
}

impl Default for LayoutConfig {
    fn default() -> Self {
        Self {
            iterations: 150,
            repulsion_strength: 1000.0,
            spring_stiffness: 0.1,
            damping: 0.8,
            theta: 0.5,
        }
    }
}

#[derive(Clone, Debug)]
pub struct OctreeNode {
    pub center_of_mass: [f32; 3],
    pub mass: f32,
    pub min: [f32; 3],
    pub max: [f32; 3],
    pub children: [u32; 8],
    pub is_leaf: bool,
    pub body_idx: u32,
}

pub struct Octree {
    pub nodes: Vec<OctreeNode>,
}

impl Octree {
    pub fn build(bodies: &[Node]) -> Self {
        if bodies.is_empty() {
            return Octree { nodes: vec![] };
        }
        
        let mut min = bodies[0].position;
        let mut max = bodies[0].position;
        for b in bodies {
            for i in 0..3 {
                min[i] = min[i].min(b.position[i]);
                max[i] = max[i].max(b.position[i]);
            }
        }
        for i in 0..3 {
            if max[i] - min[i] < 1e-4 {
                max[i] += 1e-4;
                min[i] -= 1e-4;
            }
            max[i] += 0.1;
            min[i] -= 0.1;
        }

        let mut tree = Octree {
            nodes: vec![OctreeNode {
                center_of_mass: [0.0; 3],
                mass: 0.0,
                min,
                max,
                children: [u32::MAX; 8],
                is_leaf: true,
                body_idx: u32::MAX,
            }],
        };
        tree.nodes.reserve(bodies.len() * 2);

        for (i, _) in bodies.iter().enumerate() {
            tree.insert(0, i as u32, bodies, 0);
        }
        
        tree.compute_centers(0, bodies);
        tree
    }

    fn insert(&mut self, node_idx: u32, body_idx: u32, bodies: &[Node], depth: u32) {
        if depth > 64 { return; } 
        
        let (is_leaf, current_body, min, max) = {
            let n = &self.nodes[node_idx as usize];
            (n.is_leaf, n.body_idx, n.min, n.max)
        };

        if is_leaf {
            if current_body == u32::MAX {
                self.nodes[node_idx as usize].body_idx = body_idx;
            } else {
                self.nodes[node_idx as usize].body_idx = u32::MAX;
                self.nodes[node_idx as usize].is_leaf = false;
                self.insert_to_child(min, max, node_idx, current_body, bodies, depth + 1);
                self.insert_to_child(min, max, node_idx, body_idx, bodies, depth + 1);
            }
        } else {
            self.insert_to_child(min, max, node_idx, body_idx, bodies, depth + 1);
        }
    }
    
    fn insert_to_child(&mut self, min: [f32;3], max: [f32;3], parent_idx: u32, body_idx: u32, bodies: &[Node], depth: u32) {
        let mid = [
            (min[0] + max[0]) * 0.5,
            (min[1] + max[1]) * 0.5,
            (min[2] + max[2]) * 0.5,
        ];
        let pos = bodies[body_idx as usize].position;
        
        let mut octant = 0;
        if pos[0] >= mid[0] { octant |= 1; }
        if pos[1] >= mid[1] { octant |= 2; }
        if pos[2] >= mid[2] { octant |= 4; }
        
        let child_idx = self.nodes[parent_idx as usize].children[octant];
        if child_idx == u32::MAX {
            let new_child_idx = self.nodes.len() as u32;
            self.nodes[parent_idx as usize].children[octant] = new_child_idx;
            
            let mut c_min = min;
            let mut c_max = max;
            for i in 0..3 {
                if (octant & (1 << i)) != 0 {
                    c_min[i] = mid[i];
                } else {
                    c_max[i] = mid[i];
                }
            }
            
            self.nodes.push(OctreeNode {
                center_of_mass: [0.0; 3],
                mass: 0.0,
                min: c_min,
                max: c_max,
                children: [u32::MAX; 8],
                is_leaf: true,
                body_idx: u32::MAX,
            });
            
            self.insert(new_child_idx, body_idx, bodies, depth);
        } else {
            self.insert(child_idx, body_idx, bodies, depth);
        }
    }

    fn compute_centers(&mut self, node_idx: u32, bodies: &[Node]) -> ([f32;3], f32) {
        let node = &self.nodes[node_idx as usize];
        if node.is_leaf {
            if node.body_idx != u32::MAX {
                let m = 1.0; 
                let c = bodies[node.body_idx as usize].position;
                self.nodes[node_idx as usize].mass = m;
                self.nodes[node_idx as usize].center_of_mass = c;
                return (c, m);
            }
            return ([0.0;3], 0.0);
        }
        
        let mut total_mass = 0.0;
        let mut center = [0.0; 3];
        
        for i in 0..8 {
            let child = self.nodes[node_idx as usize].children[i];
            if child != u32::MAX {
                let (c, m) = self.compute_centers(child, bodies);
                total_mass += m;
                center[0] += c[0] * m;
                center[1] += c[1] * m;
                center[2] += c[2] * m;
            }
        }
        
        if total_mass > 0.0 {
            center[0] /= total_mass;
            center[1] /= total_mass;
            center[2] /= total_mass;
        }
        self.nodes[node_idx as usize].mass = total_mass;
        self.nodes[node_idx as usize].center_of_mass = center;
        
        (center, total_mass)
    }

    pub fn compute_repulsion(&self, target_pos: [f32; 3], target_radius: f32, bodies: &[Node], config: &LayoutConfig) -> [f32; 3] {
        if self.nodes.is_empty() { return [0.0; 3]; }
        self.repulse_recursive(0, target_pos, target_radius, bodies, config)
    }
    
    fn repulse_recursive(&self, node_idx: u32, pos: [f32; 3], target_radius: f32, bodies: &[Node], config: &LayoutConfig) -> [f32; 3] {
        let node = &self.nodes[node_idx as usize];
        if node.mass == 0.0 { return [0.0; 3]; }
        
        let dx = node.center_of_mass[0] - pos[0];
        let dy = node.center_of_mass[1] - pos[1];
        let dz = node.center_of_mass[2] - pos[2];
        let dist_sq = dx*dx + dy*dy + dz*dz;
        
        let mut force = [0.0; 3];
        
        if node.is_leaf {
            if dist_sq > 0.0001 {
                let dist = dist_sq.sqrt();
                let mut f = -config.repulsion_strength * node.mass / (dist_sq + 1.0);
                
                if node.body_idx != u32::MAX {
                    let other_radius = bodies[node.body_idx as usize].radius;
                    let min_dist = target_radius + other_radius + 5.0; // padding to prevent geometric intersection
                    if dist < min_dist {
                        let overlap = min_dist - dist;
                        f -= overlap * 50.0; // gentle collision spring, not an explosion
                    }
                }
                
                let dx = node.center_of_mass[0] - pos[0];
                let dy = node.center_of_mass[1] - pos[1];
                let dz = node.center_of_mass[2] - pos[2];
                force[0] = f * (dx / dist);
                force[1] = f * (dy / dist);
                force[2] = f * (dz / dist);
            }
            return force;
        }
        
        let width = (node.max[0] - node.min[0]).max(node.max[1] - node.min[1]).max(node.max[2] - node.min[2]);
        let dist = dist_sq.sqrt();
        
        if width * width / dist_sq < config.theta * config.theta && dist > target_radius + width {
            let f = -config.repulsion_strength * node.mass / (dist_sq + 1.0);
            force[0] = f * (dx / dist);
            force[1] = f * (dy / dist);
            force[2] = f * (dz / dist);
        } else {
            for &child in &node.children {
                if child != u32::MAX {
                    let cf = self.repulse_recursive(child, pos, target_radius, bodies, config);
                    force[0] += cf[0];
                    force[1] += cf[1];
                    force[2] += cf[2];
                }
            }
        }
        force
    }
}

pub fn simulate_layout(nodes: &mut [Node], config: &LayoutConfig) {
    let mut velocities = vec![[0.0_f32; 3]; nodes.len()];
    
    for _iter in 0..config.iterations {
        let tree = Octree::build(nodes);
        
        let nodes_ref = &*nodes;
        let mut forces: Vec<[f32; 3]> = nodes_ref.par_iter().map(|node| {
            let mut force = tree.compute_repulsion(node.position, node.radius, nodes_ref, config);
            force[0] += -0.01 * node.position[0];
            force[1] += -0.01 * node.position[1];
            force[2] += -0.01 * node.position[2];
            force
        }).collect();
        
        for i in 0..nodes.len() {
            let p_idx = nodes[i].parent_index;
            if p_idx != u32::MAX && p_idx < nodes.len() as u32 {
                let dx = nodes[p_idx as usize].position[0] - nodes[i].position[0];
                let dy = nodes[p_idx as usize].position[1] - nodes[i].position[1];
                let dz = nodes[p_idx as usize].position[2] - nodes[i].position[2];
                let dist = (dx*dx + dy*dy + dz*dz).sqrt().max(0.1);
                
                let f = config.spring_stiffness * dist;
                let fx = f * (dx / dist);
                let fy = f * (dy / dist);
                let fz = f * (dz / dist);
                
                forces[i][0] += fx;
                forces[i][1] += fy;
                forces[i][2] += fz;
                
                forces[p_idx as usize][0] -= fx;
                forces[p_idx as usize][1] -= fy;
                forces[p_idx as usize][2] -= fz;
            }
        }
        
        for i in 0..nodes.len() {
            velocities[i][0] = (velocities[i][0] + forces[i][0]) * config.damping;
            velocities[i][1] = (velocities[i][1] + forces[i][1]) * config.damping;
            velocities[i][2] = (velocities[i][2] + forces[i][2]) * config.damping;
            
            let speed = (velocities[i][0].powi(2) + velocities[i][1].powi(2) + velocities[i][2].powi(2)).sqrt();
            let max_speed = 50.0;
            if speed > max_speed {
                velocities[i][0] = velocities[i][0] / speed * max_speed;
                velocities[i][1] = velocities[i][1] / speed * max_speed;
                velocities[i][2] = velocities[i][2] / speed * max_speed;
            }

            nodes[i].position[0] += velocities[i][0];
            nodes[i].position[1] += velocities[i][1];
            nodes[i].position[2] += velocities[i][2];
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_layout_config_default() {
        let config = LayoutConfig::default();
        assert_eq!(config.iterations, 150);
        assert_eq!(config.repulsion_strength, 1000.0);
        assert_eq!(config.spring_stiffness, 0.1);
        assert_eq!(config.damping, 0.8);
        assert_eq!(config.theta, 0.5);
    }

    #[test]
    fn test_octree_build_empty() {
        let tree = Octree::build(&[]);
        assert!(tree.nodes.is_empty());
    }

    #[test]
    fn test_octree_build_single() {
        let node = Node {
            position: [1.0, 2.0, 3.0],
            radius: 5.0,
            parent_index: u32::MAX,
            flags: 0,
            type_hash: 0,
            pad: 0,
        };
        let tree = Octree::build(&[node]);
        assert_eq!(tree.nodes.len(), 1);
        assert_eq!(tree.nodes[0].mass, 1.0);
        assert_eq!(tree.nodes[0].center_of_mass, [1.0, 2.0, 3.0]);
        assert!(tree.nodes[0].is_leaf);
        assert_eq!(tree.nodes[0].body_idx, 0);
    }

    #[test]
    fn test_octree_build_multiple() {
        let nodes = vec![
            Node {
                position: [0.0, 0.0, 0.0],
                radius: 1.0,
                parent_index: u32::MAX,
                flags: 0,
                type_hash: 0,
                pad: 0,
            },
            Node {
                position: [10.0, 10.0, 10.0],
                radius: 1.0,
                parent_index: u32::MAX,
                flags: 0,
                type_hash: 0,
                pad: 0,
            },
        ];
        let tree = Octree::build(&nodes);
        assert!(!tree.nodes.is_empty());
        assert_eq!(tree.nodes[0].mass, 2.0);
        assert_eq!(tree.nodes[0].center_of_mass, [5.0, 5.0, 5.0]);
    }

    #[test]
    fn test_simulate_layout_positions() {
        let mut nodes = vec![
            Node {
                position: [0.0, 0.0, 0.0],
                radius: 1.0,
                parent_index: 1,
                flags: 0,
                type_hash: 0,
                pad: 0,
            },
            Node {
                position: [2.0, 0.0, 0.0],
                radius: 1.0,
                parent_index: u32::MAX,
                flags: 0,
                type_hash: 0,
                pad: 0,
            },
        ];
        let mut config = LayoutConfig::default();
        config.iterations = 5;
        let orig_pos_0 = nodes[0].position;
        let orig_pos_1 = nodes[1].position;
        simulate_layout(&mut nodes, &config);
        
        assert_ne!(nodes[0].position, orig_pos_0);
        assert_ne!(nodes[1].position, orig_pos_1);
    }
}

