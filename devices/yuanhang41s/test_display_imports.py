from pathlib import Path
import runpy,tempfile,unittest

BOARD=Path(__file__).resolve().parent


class ImportLayout(unittest.TestCase):
    def fixture(self,root):
        files={
            'sprd_disp/sprd_drm.c':'\n\t// ret = drm_of_component_probe(&pdev->dev, compare_of, &drm_component_ops);',
            'sprd_disp/sprd_gem.c':'\tif (!(strcmp(attach->dmabuf->exp_name, "system") &&\n\t      strcmp(attach->dmabuf->exp_name, "system-uncached")))\n\t\tsprd_gem->need_iommu = true;',
            'sprd_disp/sprd_crtc.c':'\tiommu_data.buf = dma_buf->priv;',
            'iommu/sprd_iommu.c':'int sprd_iommu_map(struct device *dev, struct sprd_iommu_map_data *data) {\n\tret = sprd_iommu_get_sg(data->buf, &table);\n}\nEXPORT_SYMBOL(sprd_iommu_map);',
        }
        for name,text in files.items():
            path=root/'drivers/unisoc_platform'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
        return [root/'drivers/unisoc_platform'/name for name in files]

    def test_import_has_sg_transport_and_no_android_private_struct_cast(self):
        apply=runpy.run_path(str(BOARD/'display-imports.py'))['apply']
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);files=self.fixture(root);apply(root)
            once=[p.read_bytes() for p in files];apply(root)
            self.assertEqual(once,[p.read_bytes() for p in files])
            self.assertIn('iommu_data.table = sprd_gem->sgtb',files[2].read_text())
            self.assertNotIn('dma_buf->priv',files[2].read_text())
            self.assertIn('if (data->table)',files[3].read_text())

    def test_changed_iommu_layout_leaves_all_display_files_unchanged(self):
        apply=runpy.run_path(str(BOARD/'display-imports.py'))['apply']
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);files=self.fixture(root)
            files[-1].write_text(files[-1].read_text().replace('ret = sprd_iommu_get_sg','ret = changed_get_sg'))
            before=[p.read_bytes() for p in files]
            with self.assertRaises(ValueError):apply(root)
            self.assertEqual(before,[p.read_bytes() for p in files])


if __name__=='__main__':unittest.main()
