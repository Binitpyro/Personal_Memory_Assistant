/**
 * EnvelopingCamera.ts
 * Handles GSAP-driven interpolation for "Enveloping" smooth zooms and tracking 
 * when the user clicks a crystal (folder) or a bubble (file) within the Dreamscape.
 */

export class EnvelopingCamera {
    public position: [number, number, number] = [0, 0, 50];
    public target: [number, number, number] = [0, 0, 0];

    private isInterpolating = false;

    /**
     * Triggers a smooth enveloping transition to dive into a specific node's interior.
     */
    public envelopeToNode(nodePos: [number, number, number], nodeSize: number, animationDurationMs: number = 1000) {
        if (this.isInterpolating) return;

        // Dive into the center of the node, but keep a slight offset so we don't clip through exactly
        const destPos: [number, number, number] = [
            nodePos[0],
            nodePos[1],
            nodePos[2] + (nodeSize * 0.8) // Stop slightly back based on scale
        ];

        // Setup GSAP or requestAnimationFrame tween here.
        this.interpolate(this.position, destPos, animationDurationMs);
    }

    private interpolate(start: [number, number, number], end: [number, number, number], duration: number) {
        this.isInterpolating = true;
        const startTime = performance.now();

        const tick = (now: number) => {
            const elapsed = now - startTime;
            let t = Math.min(elapsed / duration, 1);

            // Cubic ease-in-out
            t = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

            this.position[0] = start[0] + (end[0] - start[0]) * t;
            this.position[1] = start[1] + (end[1] - start[1]) * t;
            this.position[2] = start[2] + (end[2] - start[2]) * t;

            if (t < 1) {
                requestAnimationFrame(tick);
            } else {
                this.isInterpolating = false;
            }
        };

        requestAnimationFrame(tick);
    }

    public updateViewProjectionMatrix() {
        // Create the Float32Array WebGPU bind buffer arrays here utilizing gl-matrix or similar
        // Matrix4x4 updates would live here.
        return new Float32Array(16); // Placeholder
    }
}
