from docmd.model_manager import ModelManager


def test_model_manager_has_all_product_engines():
    manager = ModelManager()
    statuses = {item.key: item for item in manager.all_status(ai_configured=False)}
    assert set(statuses) == {"paddle_ocr", "mineru", "deepseek_ocr"}
    assert statuses["paddle_ocr"].install_args
    assert statuses["deepseek_ocr"].available is False


def test_gpu_summary_is_human_readable():
    assert isinstance(ModelManager.gpu_summary(), str)


def test_device_profile_always_has_cpu_or_gpu_strategy():
    profile = ModelManager.device_profile()
    assert profile.mode in {"cpu", "gpu"}
    assert profile.detail
