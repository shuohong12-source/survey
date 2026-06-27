"""第三方增强依赖加载。

这些库不是问卷系统基础流程的必要条件：
- matplotlib：可用于后端生成静态统计图；
- openpyxl：用于导出 Excel；
- qrcode：用于生成问卷二维码。

如果某个库缺失，对应变量会设置为 None，路由中再做降级处理。
"""

import os


# matplotlib 默认可能使用 GUI 后端，服务器环境没有显示器时会报错。
# Agg 是纯图片输出后端，适合 Web 项目在后台生成 PNG。
try:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import font_manager as FontManager
    import matplotlib.pyplot as plt

    # 尝试加载 Windows 常见中文字体，避免图表标题和标签乱码。
    for FontPath in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"):
        if os.path.exists(FontPath):
            FontManager.fontManager.addfont(FontPath)
            plt.rcParams["font.sans-serif"] = [
                FontManager.FontProperties(fname=FontPath).get_name()
            ]
            break
    plt.rcParams["axes.unicode_minus"] = False
except Exception:
    plt = None

# Excel 导出依赖 openpyxl。缺失时路由会回到结果页，不影响基础统计查看。
try:
    import openpyxl
    from openpyxl.styles import Font
except Exception:
    openpyxl = None
    Font = None

# 二维码生成依赖 qrcode[pil]。缺失时二维码接口返回 204 空响应。
try:
    import qrcode
except Exception:
    qrcode = None
