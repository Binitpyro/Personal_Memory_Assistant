import React, { useEffect, useRef, useState } from 'react';
import { FileTypeTreemap } from './FileTypeTreemap';
import { WebGPURenderer } from '../renderer/WebGPURenderer';
import { getVisualizerStream, type FileEntry } from '../api';

const WebGPUCanvas = ({ allFiles }: { allFiles: Record<string, FileEntry[]> }) => {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const rendererRef = useRef<WebGPURenderer | null>(null);
    const requestRef = useRef<number>(0);
    const [isDragging, setIsDragging] = useState(false);
    const lastPos = useRef({ x: 0, y: 0 });
    const [loadError, setLoadError] = useState<string | null>(null);

    useEffect(() => {
        if (!canvasRef.current) return;

        const canvas = canvasRef.current;
        const renderer = new WebGPURenderer(canvas);
        rendererRef.current = renderer;

        let isCancelled = false;
        let resizeObserver: ResizeObserver | null = null;

        const initRenderer = async () => {
            try {
                await renderer.init();
                if (isCancelled) return;

                resizeObserver = new ResizeObserver(entries => {
                    for (let entry of entries) {
                        const { width, height } = entry.contentRect;
                        if (width > 0 && height > 0 && rendererRef.current) {
                            rendererRef.current.resize(width, height);
                        }
                    }
                });
                resizeObserver.observe(canvas);

                // 1. Fetch the data whenever allFiles changes
                const buffer = await getVisualizerStream();
                if (isCancelled) return;

                // 2. Check the data payload
                if (buffer.byteLength > 4) {
                    const headerView = new Uint8Array(buffer, 0, 4);

                    console.log("FIRST 4 BYTES FROM BACKEND:", headerView);

                    // 60, 33 is "<!" from "<!DOCTYPE html>"
                    if (headerView[0] === 60 && headerView[1] === 33) {
                        console.error("VITE TRAP: The backend sent HTML instead of Binary 3D Data!");
                        if (rendererRef.current) rendererRef.current.destroy();
                        if (requestRef.current) cancelAnimationFrame(requestRef.current);
                        if (resizeObserver) resizeObserver.disconnect();
                        setLoadError("Backend disconnected. Vite sent HTML.");
                        return;
                    }

                    await renderer.loadData(buffer);
                } else {
                    if (!isCancelled) {
                        if (rendererRef.current) rendererRef.current.destroy();
                        if (requestRef.current) cancelAnimationFrame(requestRef.current);
                        if (resizeObserver) resizeObserver.disconnect();
                        setLoadError("No 3D data available. Please index some files first.");
                    }
                    return;
                }

                // 3. Start the animation loop
                const animate = () => {
                    renderer.render();
                    requestRef.current = requestAnimationFrame(animate);
                };
                requestRef.current = requestAnimationFrame(animate);

            } catch (err) {
                console.error("Failed to initialize WebGPU:", err);
                if (!isCancelled) {
                    if (rendererRef.current) rendererRef.current.destroy();
                    if (requestRef.current) cancelAnimationFrame(requestRef.current);
                    if (resizeObserver) resizeObserver.disconnect();
                    setLoadError(err instanceof Error ? err.message : "Unknown error loading 3D data");
                }
            }
        };

        initRenderer();

        return () => {
            isCancelled = true;
            if (requestRef.current) cancelAnimationFrame(requestRef.current);
            if (resizeObserver) resizeObserver.disconnect();
            if (rendererRef.current) {
                rendererRef.current.destroy();
            }
        };
    }, [allFiles]);

    const handleMouseDown = (e: React.MouseEvent) => {
        setIsDragging(true);
        lastPos.current = { x: e.clientX, y: e.clientY };
    };

    const handleMouseMove = (e: React.MouseEvent) => {
        if (!isDragging || !rendererRef.current) return;
        const dx = e.clientX - lastPos.current.x;
        const dy = e.clientY - lastPos.current.y;
        rendererRef.current.handleMouseMove(dx, dy);
        lastPos.current = { x: e.clientX, y: e.clientY };
    };

    const handleMouseUp = () => setIsDragging(false);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;

        const handleNativeWheel = (e: WheelEvent) => {
            e.preventDefault();
            e.stopPropagation();
            if (rendererRef.current) {
                rendererRef.current.handleZoom(e.deltaY);
            }
        };

        // Passive: false allows e.preventDefault() to actually stop the page from scrolling
        canvas.addEventListener('wheel', handleNativeWheel, { passive: false });

        return () => {
            canvas.removeEventListener('wheel', handleNativeWheel);
        };
    }, []);

    if (loadError) {
        return (
            <div className="w-full h-full min-h-[400px] flex items-center justify-center bg-error/5 text-error rounded-3xl border border-error/20">
                <div className="text-center p-6">
                    <p className="font-bold mb-2">Failed to load Crystal Dreamscape</p>
                    <p className="text-xs opacity-80">{loadError}</p>
                </div>
            </div>
        );
    }

    return (
        <div className="w-full h-full min-h-[400px] relative bg-[#f1f5e0] rounded-3xl overflow-hidden border border-white/40 shadow-inner">
            <div className="absolute top-6 left-8 z-10 pointer-events-none">
                <h2 className="text-2xl font-bold text-primary flex items-center gap-3">
                    <span className="w-3 h-3 bg-accent rounded-full animate-pulse shadow-[0_0_12px_rgba(142,72,234,0.6)]" />
                    Crystal Dreamscape 3D
                </h2>
                <p className="text-text-secondary text-[10px] font-bold mt-2 tracking-widest uppercase opacity-60">
                    DreamScape 3D
                </p>
            </div>
            <canvas
                ref={canvasRef}
                className="w-full h-full cursor-grab active:cursor-grabbing block"
                style={{ minHeight: '400px', height: '100%', width: '100%' }}
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

    useEffect(() => {
        const checkGPU = async () => {
            if (!navigator.gpu) {
                setStatus('unsupported');
                return;
            }
            try {
                const adapter = await navigator.gpu.requestAdapter();
                if (!adapter) {
                    setStatus('unsupported');
                    return;
                }
                setStatus('supported');
            } catch (e) {
                console.error("WebGPU initialization failed: ", e);
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
                        <span className="font-bold">WebGPU Not Available:</span> Your browser does not support WebGPU or it is disabled. Falling back to 2D Hardware-Accelerated Charts.
                    </p>
                </div>
                <div className="flex-1 min-h-[600px]">
                    <FileTypeTreemap
                        allFiles={allFiles}
                        activeFilter={activeFilter}
                        onFilterChange={onFilterChange}
                        initialMode={initialMode}
                    />
                </div>
            </div>
        );
    }
    return <WebGPUCanvas allFiles={allFiles} />;
};

export default WebGPUFallback;