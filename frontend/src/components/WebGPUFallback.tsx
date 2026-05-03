import React, { useEffect, useRef, useState, useCallback } from 'react';
import { FileTypeTreemap } from './FileTypeTreemap';
import { WebGPURenderer } from '../renderer/WebGPURenderer';
import { getVisualizerStream, type FileEntry } from '../api';

interface WebGPUCanvasProps {
    allFiles: Record<string, FileEntry[]>;
    activeFilter?: string | null;
    onError: (errorMsg: string) => void;
}

const WebGPUCanvas: React.FC<WebGPUCanvasProps> = ({ allFiles, activeFilter, onError }) => {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const rendererRef = useRef<WebGPURenderer | null>(null);
    const requestRef = useRef<number>(0);
    const [isDragging, setIsDragging] = useState(false);
    const lastPos = useRef({ x: 0, y: 0 });
    const dragStartPos = useRef({ x: 0, y: 0 });

    const initRenderer = useCallback(async (canvas: HTMLCanvasElement) => {
        const renderer = new WebGPURenderer(canvas);
        rendererRef.current = renderer;
        try {
            await renderer.init();
            const buffer = await getVisualizerStream(activeFilter);
            if (buffer.byteLength > 4) {
                const header = new Uint8Array(buffer, 0, 2);
                if (header[0] === 60 && header[1] === 33) throw new Error("Backend sent HTML. Vite trap detected.");
                await renderer.loadData(buffer);
            } else {
                throw new Error("No 3D data available or filter returned 0 results.");
            }

            const animate = () => {
                renderer.render();
                requestRef.current = requestAnimationFrame(animate);
            };
            requestRef.current = requestAnimationFrame(animate);
            return renderer;
        } catch (err) {
            onError(err instanceof Error ? err.message : "Unknown error loading 3D data");
            return null;
        }
    }, [activeFilter, onError]);

    useEffect(() => {
        if (!canvasRef.current) return;
        const canvas = canvasRef.current;
        let resizeObserver: ResizeObserver | null = null;

        initRenderer(canvas).then(renderer => {
            if (!renderer) return;
            resizeObserver = new ResizeObserver(entries => {
                for (const entry of entries) {
                    const { width, height } = entry.contentRect;
                    if (width > 0 && height > 0) renderer.resize(width, height);
                }
            });
            resizeObserver.observe(canvas);
        });

        return () => {
            if (requestRef.current) cancelAnimationFrame(requestRef.current);
            if (resizeObserver) resizeObserver.disconnect();
            if (rendererRef.current) rendererRef.current.destroy();
        };
    }, [allFiles, activeFilter, initRenderer]);

    const handleMouseDown = (e: React.MouseEvent) => {
        setIsDragging(true);
        lastPos.current = { x: e.clientX, y: e.clientY };
        dragStartPos.current = { x: e.clientX, y: e.clientY };
    };

    const handleMouseMove = (e: React.MouseEvent) => {
        if (!isDragging || !rendererRef.current) return;
        rendererRef.current.handleMouseMove(e.clientX - lastPos.current.x, e.clientY - lastPos.current.y);
        lastPos.current = { x: e.clientX, y: e.clientY };
    };

    const handleMouseUp = async (e: React.MouseEvent) => {
        setIsDragging(false);
        const dx = Math.abs(e.clientX - dragStartPos.current.x);
        const dy = Math.abs(e.clientY - dragStartPos.current.y);
        if (dx < 5 && dy < 5 && rendererRef.current && canvasRef.current) {
            const rect = canvasRef.current.getBoundingClientRect();
            const hash = await rendererRef.current.pick(e.clientX - rect.left, e.clientY - rect.top);
            if (hash !== null) console.log("Picked 3D Node Hash:", hash);
        }
    };

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const onWheel = (e: WheelEvent) => {
            e.preventDefault();
            e.stopPropagation();
            rendererRef.current?.handleZoom(e.deltaY);
        };
        canvas.addEventListener('wheel', onWheel, { passive: false });
        return () => canvas.removeEventListener('wheel', onWheel);
    }, []);

    return (
        <div className="w-full h-full min-h-[400px] relative bg-[#f1f5e0] rounded-3xl overflow-hidden border border-white/40 shadow-inner">
            <div className="absolute top-6 left-8 z-10 pointer-events-none">
                <h2 className="text-2xl font-bold text-primary flex items-center gap-3">
                    <span className="w-3 h-3 bg-accent rounded-full animate-pulse shadow-[0_0_12px_rgba(142,72,234,0.6)]" />{' '}
                    Crystal Dreamscape 3D
                </h2>
                <p className="text-text-secondary text-[10px] font-bold mt-2 tracking-widest uppercase opacity-60">DreamScape 3D</p>
            </div>
            <canvas
                ref={canvasRef}
                className="w-full h-full cursor-grab active:cursor-grabbing block touch-none"
                style={{ minHeight: '400px', height: '100%', width: '100%', touchAction: 'none' }}
                onMouseDown={handleMouseDown}
                onMouseMove={handleMouseMove}
                onMouseUp={handleMouseUp}
                onMouseLeave={handleMouseUp}
            />
        </div>
    );
};

interface WebGPUFallbackProps {
    allFiles: Record<string, FileEntry[]>;
    activeFilter?: string | null;
    onFilterChange?: (ext: string | null) => void;
    initialMode?: 'folder' | 'type';
}

export const WebGPUFallback: React.FC<WebGPUFallbackProps> = ({ allFiles, activeFilter, onFilterChange, initialMode }) => {
    const [status, setStatus] = useState<'checking' | 'supported' | 'unsupported'>('checking');
    const [fallbackReason, setFallbackReason] = useState<string | null>(null);

    useEffect(() => {
        const checkGPU = async () => {
            if (!navigator.gpu) {
                setFallbackReason("Browser doesn't support WebGPU.");
                setStatus('unsupported');
                return;
            }
            try {
                const adapter = await navigator.gpu.requestAdapter();
                if (adapter) {
                    setStatus('supported');
                } else {
                    setFallbackReason("No appropriate GPU Adapter found.");
                    setStatus('unsupported');
                }
            } catch (e) {
                console.error("GPU Check Error:", e);
                setFallbackReason("WebGPU initialization failed.");
                setStatus('unsupported');
            }
        };
        checkGPU();
    }, []);

    if (status === 'checking') {
        return (
            <div className="w-full h-[600px] bg-slate-900 flex items-center justify-center rounded-lg border border-slate-800">
                <div className="flex flex-col items-center">
                    <div className="w-12 h-12 border-4 border-blue-500/30 border-t-blue-500 rounded-full animate-spin"></div>
                    <p className="mt-4 text-slate-400 font-mono text-sm">Initializing GPU Infrastructure...</p>
                </div>
            </div>
        );
    }

    if (status === 'unsupported') {
        return (
            <div className="w-full h-full flex flex-col">
                <div className="bg-amber-900/30 border-l-4 border-amber-500 p-4 mb-4">
                    <p className="text-amber-200 text-sm">
                        <span className="font-bold">2D Hardware-Accelerated View:</span> {fallbackReason || "WebGPU Not Available"}
                    </p>
                </div>
                <div className="flex-1 min-h-[400px]">
                    <FileTypeTreemap allFiles={allFiles} activeFilter={activeFilter} onFilterChange={onFilterChange} initialMode={initialMode} />
                </div>
            </div>
        );
    }

    return <WebGPUCanvas allFiles={allFiles} activeFilter={activeFilter} onError={(msg) => { setFallbackReason(`3D Stream Error: ${msg}`); setStatus('unsupported'); }} />;
};

export default WebGPUFallback;
