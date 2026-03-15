import React, { useEffect, useRef, useState } from 'react';
import { FileTypeTreemap } from './FileTypeTreemap';
import { WebGPURenderer } from '../renderer/WebGPURenderer';
import { getVisualizerStream, type FileEntry } from '../api';

// A WebGPU canvas component that actually renders
const WebGPUCanvas = () => {
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

        const initRenderer = async () => {
            try {
                await renderer.init();
                if (isCancelled) return;

                const buffer = await getVisualizerStream();
                if (isCancelled) return;

                if (buffer.byteLength > 4) {
                    await renderer.loadData(buffer);
                }

                const animate = () => {
                    renderer.render();
                    requestRef.current = requestAnimationFrame(animate);
                };
                requestRef.current = requestAnimationFrame(animate);
            } catch (err) {
                console.error("Failed to initialize or fetch WebGPU data:", err);
                if (!isCancelled) {
                    setLoadError(err instanceof Error ? err.message : "Unknown error loading 3D data");
                }
            }
        };

        initRenderer();

        return () => {
            isCancelled = true;
            if (requestRef.current) cancelAnimationFrame(requestRef.current);
        };
    }, []);

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

    const handleWheel = (e: React.WheelEvent) => {
        if (rendererRef.current) rendererRef.current.handleZoom(e.deltaY);
    };

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
        <div className="w-full h-full relative bg-[#f1f5e0] rounded-3xl overflow-hidden border border-white/40 shadow-inner">
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
                className="w-full h-full cursor-grab active:cursor-grabbing" 
                style={{ minHeight: '400px', display: 'block' }}
                onMouseDown={handleMouseDown}
                onMouseMove={handleMouseMove}
                onMouseUp={handleMouseUp}
                onMouseLeave={handleMouseUp}
                onWheel={handleWheel}
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
    return <WebGPUCanvas />;
};

export default WebGPUFallback;
