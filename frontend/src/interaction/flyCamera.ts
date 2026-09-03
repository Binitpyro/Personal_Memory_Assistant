/**
 * flyCamera.ts
 *
 * The camera-basis maths behind WASD/QE flythrough, shared by both renderer
 * tiers.
 *
 * Both renderers run an ORBIT rig: `updateCamera` derives the eye from the
 * pivot as
 *
 *     eye = pivot + zoom * (cos(rx)·sin(ry), sin(rx), cos(rx)·cos(ry))
 *
 * so the eye is not independent state. That makes flying cheap — translating
 * the PIVOT moves the whole rig, and orbit, dolly, `focusOnNode`, the
 * zoom-derived fog density and the particle spawn radius all keep working
 * untouched. Maya and Blender's "walk" relative to the interest point behaves
 * the same way.
 *
 * This lives here rather than being copy-pasted into each renderer because the
 * basis has to agree with `updateCamera` exactly and in both tiers; a sign
 * error in one copy is the kind of bug that only shows up on the fallback
 * path, on someone else's GPU.
 */

export type Vec3 = readonly [number, number, number];

export interface CameraBasis {
    /** Unit vector the camera looks along, pivot-ward from the eye. */
    readonly forward: Vec3;
    /** Unit vector to the camera's right, always horizontal. */
    readonly right: Vec3;
}

/**
 * Camera basis for the current orbit angles.
 *
 * `right` is `normalize(cross(forward, worldUp))`; the `cos(rx)` factor
 * divides out, which is why it carries no elevation term. Both renderers clamp
 * `rotationX` to ±(π/2 − 0.1), so that division is never by zero.
 */
export function cameraBasis(rotationX: number, rotationY: number): CameraBasis {
    const cx = Math.cos(rotationX);
    const sx = Math.sin(rotationX);
    const cy = Math.cos(rotationY);
    const sy = Math.sin(rotationY);

    return {
        forward: [-cx * sy, -sx, -cx * cy],
        right: [cy, 0, -sy],
    };
}

/**
 * Where the pivot lands after one fly step.
 *
 * `up` is WORLD up, not the camera's — Q/E in both Unreal and Unity rise and
 * fall vertically regardless of where the camera is pointing, which is what
 * stops a downward-tilted view from "flying into the floor" when you ask to go
 * up.
 *
 * The step scales with `zoom`, so moving feels the same whether the user is
 * inside one folder or looking at the whole corpus — the distance-scaled
 * camera speed Unreal uses. `limit` keeps a held key from flinging the pivot
 * somewhere with nothing to look at and no way back but Home.
 */
export function flyStep(
    pivot: Vec3,
    rotationX: number,
    rotationY: number,
    zoom: number,
    move: { forward: number; right: number; up: number },
    limit: number,
): Vec3 {
    const { forward, right } = cameraBasis(rotationX, rotationY);
    const step = Math.max(1, zoom * 0.02);

    const clamp = (v: number) => Math.max(-limit, Math.min(limit, v));

    return [
        clamp(pivot[0] + (forward[0] * move.forward + right[0] * move.right) * step),
        clamp(pivot[1] + (forward[1] * move.forward + move.up) * step),
        clamp(pivot[2] + (forward[2] * move.forward + right[2] * move.right) * step),
    ];
}

/**
 * Where the eye should sit this frame, given where it is and where the rig
 * wants it.
 *
 * The camera glides toward its target rather than cutting, which reads well
 * when the pivot jumps — `focusOnNode` moving to a new node, say. Under
 * `prefers-reduced-motion` that glide is exactly the kind of animation the
 * user asked not to have, so it becomes a cut.
 *
 * Snapping is not only a preference question, it is a correctness one. The
 * render loop parks itself when there is no input and motion is reduced, so a
 * glide would render a single frame — one step of the interpolation — and then
 * stop, stranding the camera partway to a node it was asked to look at. A cut
 * arrives in that one frame.
 */
export function approachEye(current: Vec3, target: Vec3, smooth: boolean, factor: number): Vec3 {
    if (!smooth) return target;
    return [
        current[0] + (target[0] - current[0]) * factor,
        current[1] + (target[1] - current[1]) * factor,
        current[2] + (target[2] - current[2]) * factor,
    ];
}
