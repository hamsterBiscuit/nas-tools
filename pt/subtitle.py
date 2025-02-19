import os.path

import requests
from pythonopensubtitles.opensubtitles import OpenSubtitles

import log
from config import Config
from utils.types import MediaType


class Subtitle:
    __server = None
    __username = None
    __password = None
    __host = None
    __api_key = None
    __ost = None

    def __init__(self):
        self.init_config()

    def init_config(self):
        config = Config()
        subtitle = config.get_config('subtitle')
        if subtitle:
            self.__server = subtitle.get("server")
            if self.__server == "opensubtitles":
                self.__username = subtitle.get("opensubtitles", {}).get("username")
                self.__password = subtitle.get("opensubtitles", {}).get("password")
                if self.__username and self.__password:
                    self.__ost = OpenSubtitles()
                    try:
                        self.__ost.login(self.__username, self.__password)
                    except Exception as e:
                        log.error("【SUBTITLE】Opensubtitles.org登录失败：%s" % str(e))
                        self.__ost = None
            elif self.__server == "chinesesubfinder":
                self.__api_key = subtitle.get("chinesesubfinder", {}).get("api_key")
                self.__host = subtitle.get("chinesesubfinder", {}).get('host')
                if not self.__host.startswith('http://') and not self.__host.startswith('https://'):
                    self.__host = "http://" + self.__host
                if not self.__host.endswith('/'):
                    self.__host = self.__host + "/"

    def download_subtitle(self, items):
        """
        字幕下载入口
        :param items: {"type":, "file", "file_ext":, "name":, "title", "year":, "season":, "episode":, "bluray":}
        """
        if not self.__server:
            return
        if not items:
            return
        if self.__server == "opensubtitles":
            self.__download_opensubtitles(items)
        elif self.__server == "chinesesubfinder":
            self.__download_chinesesubfinder(items)

    def __download_opensubtitles(self, items):
        """
        调用OpenSubtitles Api下载字幕
        """
        if not self.__ost:
            return
        # 字幕缓存，避免重复下载
        subtitles_cache = {}
        for item in items:
            if not item:
                continue
            if not item.get("name") or not item.get("file"):
                continue
            subtitles = subtitles_cache.get(item.get("name"))
            if subtitles is None:
                log.info("【SUBTITLE】开始从Opensubtitle.org检索字幕: %s" % item.get("name"))
                subtitles = self.__ost.search_subtitles([{'sublanguageid': 'chi', 'query': item.get("name")}])
                if not subtitles:
                    subtitles_cache[item.get("name")] = []
                    log.info("【SUBTITLE】%s 未检索到字幕" % item.get("name"))
                else:
                    subtitles_cache[item.get("name")] = subtitles
                    log.info("【SUBTITLE】Opensubtitles.org返回数据：%s" % len(subtitles))
            if not subtitles:
                continue
            success_flag = False
            for subtitle in subtitles:
                # 字幕ID
                IDSubtitleFile = subtitle.get('IDSubtitleFile')
                # 年份
                MovieYear = subtitle.get('MovieYear')
                if item.get('year') and str(MovieYear) != str(item.get('year')):
                    continue
                # 季
                SeriesSeason = subtitle.get('SeriesSeason')
                if item.get('season') and int(SeriesSeason) != int(item.get('season')):
                    continue
                # 集
                SeriesEpisode = subtitle.get('SeriesEpisode')
                if item.get('episode') and int(SeriesEpisode) != int(item.get('episode')):
                    continue
                # 字幕文件名
                SubFileName = subtitle.get('SubFileName')
                # 字幕格式
                SubFormat = subtitle.get('SubFormat')
                # 下载后的字幕文件路径
                Download_File = "%s.zh-cn.%s" % (os.path.basename(item.get("file")), SubFormat)
                # 下载目录
                Download_Dir = os.path.dirname(item.get("file"))
                # 文件存在不下载
                if os.path.exists(os.path.join(Download_Dir, Download_File)):
                    continue
                log.info("【SUBTITLE】正在从Opensubtitles.org下载字幕 %s 到 %s " % (SubFileName, Download_File))
                self.__ost.download_subtitles([IDSubtitleFile], override_filenames={IDSubtitleFile: Download_File}, output_directory=Download_Dir)
                success_flag = True
            if not success_flag:
                if item.get('episode'):
                    log.info("【SUBTITLE】%s 季：%s 集：%s 未找到符合条件的字幕" % (item.get("name"), item.get("season"), item.get("episode")))
                else:
                    log.info("【SUBTITLE】%s 未找到符合条件的字幕" % item.get("name"))

    def __download_chinesesubfinder(self, items):
        """
        调用ChineseSubFinder下载字幕
        """
        if not self.__host or not self.__api_key:
            return
        req_url = "%sapi/v1/add-job" % self.__host
        notify_items = []
        for item in items:
            if not item:
                continue
            if not item.get("name") or not item.get("file"):
                continue
            if item.get("bluray"):
                file_path = "%s.mp4" % item.get("file")
            else:
                file_path = "%s%s" % (item.get("file"), item.get("file_ext"))
            # 一个名称只建一个任务
            if file_path not in notify_items:
                notify_items.append(file_path)
                log.info("【SUBTITLE】通知ChineseSubFinder下载字幕: %s" % file_path)
                params = {
                    "video_type": 0 if item.get("type") == MediaType.MOVIE else 1,
                    "physical_video_file_full_path": file_path,
                    "task_priority_level": 3,
                    "media_server_inside_video_id": "",
                    "is_bluray": item.get("bluray")
                }
                try:
                    res = requests.post(req_url, headers={"Authorization": "Bearer %s" % self.__api_key}, json=params, timeout=10)
                    if not res or res.status_code != 200:
                        log.error("【SUBTITLE】调用ChineseSubFinder API失败！")
                    else:
                        job_id = res.json().get("job_id")
                        message = res.json().get("message")
                        if not job_id:
                            log.warn("【SUBTITLE】ChineseSubFinder下载字幕出错：%s" % message)
                        else:
                            log.info("【SUBTITLE】ChineseSubFinder任务添加成功：%s" % job_id)
                except Exception as e:
                    log.error("【SUBTITLE】连接ChineseSubFinder出错：" + str(e))
