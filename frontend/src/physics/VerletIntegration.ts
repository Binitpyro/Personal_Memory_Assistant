/**
 * VerletIntegration.ts
 * CPU-side logic for "bouncy/malleable" interactions inside the Crystal Dreamscape.
 * Offloaded to WebGPU buffer modifiers or resolved completely as uniform offsets.
 */

export interface Particle {
    pos: [number, number, number];
    oldPos: [number, number, number];
    acc: [number, number, number];
    mass: number;
}

export class VerletIntegration {
    private readonly particles: Particle[] = [];
    private readonly gravity: [number, number, number] = [0, -9.81, 0];
    private readonly dt: number = 1 / 60;

    public addParticle(x: number, y: number, z: number, mass: number = 1) {
        this.particles.push({
            pos: [x, y, z],
            oldPos: [x, y, z],
            acc: [0, 0, 0],
            mass
        });
    }

    public applyForce(index: number, fx: number, fy: number, fz: number) {
        if (index < 0 || index >= this.particles.length) return;
        const p = this.particles[index];
        p.acc[0] += fx / p.mass;
        p.acc[1] += fy / p.mass;
        p.acc[2] += fz / p.mass;
    }

    // Apply repulsive forces to mimic "malleability" of bubbles when mouse hovers
    public applyHoverRepulsion(index: number, cursorX: number, cursorY: number, cursorZ: number, radius: number, strength: number) {
        if (index < 0 || index >= this.particles.length) return;
        const p = this.particles[index];
        const dx = p.pos[0] - cursorX;
        const dy = p.pos[1] - cursorY;
        const dz = p.pos[2] - cursorZ;

        const distSq = dx * dx + dy * dy + dz * dz;
        if (distSq < radius * radius && distSq > 0.0001) {
            const dist = Math.sqrt(distSq);
            const force = (radius - dist) / radius * strength;

            this.applyForce(index, (dx / dist) * force, (dy / dist) * force, (dz / dist) * force);
        }
    }

    public step() {
        const dtSq = this.dt * this.dt;

        for (const p of this.particles) {

            // Add gravity
            p.acc[0] += this.gravity[0];
            p.acc[1] += this.gravity[1];
            p.acc[2] += this.gravity[2];

            // Velocity Verlet Integration
            const vx = p.pos[0] - p.oldPos[0];
            const vy = p.pos[1] - p.oldPos[1];
            const vz = p.pos[2] - p.oldPos[2];

            p.oldPos[0] = p.pos[0];
            p.oldPos[1] = p.pos[1];
            p.oldPos[2] = p.pos[2];

            // V = oldV + A * dt^2
            p.pos[0] += vx + p.acc[0] * dtSq;
            p.pos[1] += vy + p.acc[1] * dtSq;
            p.pos[2] += vz + p.acc[2] * dtSq;

            // Reset acceleration
            p.acc[0] = 0;
            p.acc[1] = 0;
            p.acc[2] = 0;

            // Basic floor constraint
            if (p.pos[1] < 0) {
                p.pos[1] = 0;
                // Add fake friction/damping for bounce
                p.oldPos[0] = p.pos[0] - vx * 0.8;
                p.oldPos[2] = p.pos[2] - vz * 0.8;
            }
        }
    }
}
