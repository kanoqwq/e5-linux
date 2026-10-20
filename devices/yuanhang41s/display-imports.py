"""Map imported DRM buffers through this board's DPU IOMMU.

The factory DT gives DISPC a 0x10000000..0x1fffffff IOVA window. Coherent
framebuffers still need 32-bit DMA. Imported Panfrost buffers can reside above
4 GiB; passing their already-known SG table avoids assuming an Android
system_heap_buffer layout for a DRM exporter's private object.
"""
from pathlib import Path


def apply(kernel):
    kernel=Path(kernel);base=kernel/'drivers/unisoc_platform/sprd_disp'
    edits=[]
    def edit(path,old,new):
        source=path.read_text()
        if source.count(new)==1 and source.count(old)==new.count(old):return
        if source.count(old)!=1 or new in source:raise ValueError('Unexpected imported-buffer source: '+path.name)
        edits.append((path,source.replace(old,new)))
    edit(base/'sprd_drm.c',
        '\n\t// ret = drm_of_component_probe(&pdev->dev, compare_of, &drm_component_ops);',
        '''
\t/* 41S: coherent scanout stays 32-bit; imports use the DPU IOMMU. */
\tret = dma_set_mask(&pdev->dev, DMA_BIT_MASK(64));
\tif (ret)
\t\treturn ret;

\t// ret = drm_of_component_probe(&pdev->dev, compare_of, &drm_component_ops);''')
    edit(base/'sprd_gem.c',
        '\tif (!(strcmp(attach->dmabuf->exp_name, "system") &&\n'
        '\t      strcmp(attach->dmabuf->exp_name, "system-uncached")))\n'
        '\t\tsprd_gem->need_iommu = true;',
        '\t/* 41S: every imported SG, including DRM/Panfrost, gets a DPU IOVA. */\n'
        '\tsprd_gem->need_iommu = true;')
    edit(base/'sprd_crtc.c','\tiommu_data.buf = dma_buf->priv;',
        '\t/* 41S: a DRM exporter is not an Android system_heap_buffer. */\n'
        '\tiommu_data.buf = dma_buf;\n\tiommu_data.table = sprd_gem->sgtb;')
    path=kernel/'drivers/unisoc_platform/iommu/sprd_iommu.c';source=path.read_text()
    start=source.index('int sprd_iommu_map(struct device *dev,');end=source.index('\nEXPORT_SYMBOL(sprd_iommu_map);',start)
    body=source[start:end]
    old='\tret = sprd_iommu_get_sg(data->buf, &table);'
    new='''\t/* Explicit importer SG tables do not expose exporter-private layouts. */
\tif (data->table) {
\t\ttable = data->table;
\t\tret = 0;
\t} else {
\t\tret = sprd_iommu_get_sg(data->buf, &table);
\t}'''
    if new not in body:
        if body.count(old)!=1:raise ValueError('Unexpected legacy IOMMU map')
        edits.append((path,source[:start]+body.replace(old,new)+source[end:]))
    for path,source in edits:path.write_text(source)


if __name__=='__main__':
    import sys
    apply(sys.argv[1])
