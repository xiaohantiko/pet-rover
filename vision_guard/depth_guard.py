"""Read-only evidence from a rectified optical-axis depth image in millimetres."""
import argparse
from collections import deque
from dataclasses import dataclass
import json
import math
from pathlib import Path


@dataclass(frozen=True)
class Calibration:
    camera_id: str
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    roi: tuple
    min_mm: float
    stop_mm: float
    max_mm: float
    min_cluster: int
    max_age_ms: float
    min_coverage: float = .85
    min_tile_coverage: float = .65

    def validate(self):
        dimensions = (self.width,self.height,self.min_cluster)
        if not self.camera_id or not all(type(v) is int and v>0 for v in dimensions):
            raise ValueError("需要有效相机标识、图像尺寸及连通像素阈值")
        if len(self.roi)!=4 or not all(type(v) is int for v in self.roi):
            raise ValueError("ROI 须为四个整数")
        left,top,right,bottom=self.roi
        if not (0<=left<right<=self.width and 0<=top<bottom<=self.height and
                right-left>=4 and bottom-top>=3 and self.min_cluster<=(right-left)*(bottom-top)):
            raise ValueError("ROI 超界、过小或聚簇阈值不合理")
        numbers=(self.fx,self.fy,self.cx,self.cy,self.min_mm,self.stop_mm,self.max_mm,
                 self.max_age_ms,self.min_coverage,self.min_tile_coverage)
        if not all(isinstance(v,(float,int)) and math.isfinite(v) for v in numbers):
            raise ValueError("标定值须为有限数")
        if not (self.fx>0 and self.fy>0 and 0<=self.cx<self.width and 0<=self.cy<self.height and
                0<self.min_mm<self.stop_mm<self.max_mm and self.max_age_ms>0 and
                0<self.min_tile_coverage<=self.min_coverage<=1):
            raise ValueError("内参、距离、时间或覆盖率门槛无效")


def classify(depth_mm, calibration, *, camera_id, age_ms):
    cal=calibration
    cal.validate()
    result={"state":"unknown","reason":"","roi":list(cal.roi),"valid_fraction":None,
            "worst_tile_fraction":None,"nearest_valid_mm":None,"largest_close_cluster_px":0,
            "cluster_sample_camera_xyz_mm":None,"motion_authorization":False}
    if camera_id!=cal.camera_id:
        result["reason"]="calibration_mismatch"
        return result
    if not isinstance(age_ms,(int,float)) or not math.isfinite(age_ms) or not 0<=age_ms<=cal.max_age_ms:
        result["reason"]="missing_or_stale_frame"
        return result
    try:
        shape_ok=(len(depth_mm)==cal.height and all(len(row)==cal.width for row in depth_mm))
    except TypeError:
        shape_ok=False
    if not shape_ok:
        result["reason"]="shape_mismatch"
        return result
    left,top,right,bottom=cal.roi
    tiles=[[0,0] for _ in range(12)]
    close=set()
    valid,too_close,closest=0,0,math.inf
    for row in range(top,bottom):
        for col in range(left,right):
            tile=((row-top)*3//(bottom-top))*4+(col-left)*4//(right-left)
            tiles[tile][1]+=1
            raw=depth_mm[row][col]
            if raw is None or isinstance(raw,(str,bytes,bool)):
                continue
            try:
                z=float(raw)
            except (ValueError,TypeError,OverflowError):
                continue
            if not math.isfinite(z) or z<=0 or z>cal.max_mm:
                continue
            if z<cal.min_mm:
                too_close+=1
                continue
            valid+=1
            tiles[tile][0]+=1
            closest=min(closest,z)
            if z<=cal.stop_mm:
                close.add((col,row))
    any_close=bool(close)
    coverage=valid/((right-left)*(bottom-top))
    worst=min(good/count for good,count in tiles)
    result.update(valid_fraction=round(coverage,4),worst_tile_fraction=round(worst,4),
                  nearest_valid_mm=closest if valid else None)
    while close:
        seed=close.pop()
        count=0
        nearest=seed
        queue=deque([seed])
        while queue:
            col,row=queue.popleft()
            count+=1
            if ((float(depth_mm[row][col]),row,col) <
                    (float(depth_mm[nearest[1]][nearest[0]]),nearest[1],nearest[0])):
                nearest=(col,row)
            for neighbor in ((col-1,row),(col+1,row),(col,row-1),(col,row+1)):
                if neighbor in close:
                    close.remove(neighbor)
                    queue.append(neighbor)
        if count>result["largest_close_cluster_px"]:
            result["largest_close_cluster_px"]=count
            col,row=nearest
            z=float(depth_mm[row][col])
            result["cluster_sample_camera_xyz_mm"]=[round((col-cal.cx)*z/cal.fx,2),
                                                     round((row-cal.cy)*z/cal.fy,2),round(z,2)]
    if result["largest_close_cluster_px"]>=cal.min_cluster:
        result.update(state="stop",reason="connected_close_pixels")
    elif too_close:
        result["reason"]="below_minimum_range"
    elif any_close:
        result["reason"]="sparse_close_returns"
    elif coverage<cal.min_coverage or worst<cal.min_tile_coverage:
        result["reason"]="insufficient_depth_coverage"
    else:
        result.update(state="clear",reason="no_cluster_in_observed_roi")
    return result


def fixtures():
    cal=Calibration("synthetic_depth",16,12,16,16,7.5,5.5,(2,2,14,11),100,400,3000,4,200)
    clear=[[1200]*16 for _ in range(12)]
    blocked=[row.copy() for row in clear]
    for row in (5,6):
        for col in (7,8):
            blocked[row][col]=300
    blind=[row.copy() for row in clear]
    for row in (2,3,4):
        for col in (2,3,4):
            blind[row][col]=None
    return cal,{"far":clear,"close":blocked,"blind_tile":blind}


def main():
    parser=argparse.ArgumentParser(description="只读深度 ROI 离线重放；没有运动输出")
    parser.add_argument("--calibration",type=Path)
    parser.add_argument("--frame",type=Path)
    args=parser.parse_args()
    if bool(args.calibration)!=bool(args.frame):
        parser.error("--calibration 与 --frame 必须一起指定")
    try:
        if args.frame:
            config=Calibration(**json.loads(args.calibration.read_text(encoding="utf-8")))
            item=json.loads(args.frame.read_text(encoding="utf-8"))
            result=classify(item["depth_mm"],config,camera_id=item["camera_id"],age_ms=item["age_ms"])
        else:
            config,scenes=fixtures()
            result={name:classify(frame,config,camera_id=config.camera_id,age_ms=0)
                    for name,frame in scenes.items()}
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,TypeError,KeyError,OSError) as exc:
        parser.exit(2,f"无法判断；按 unknown 处理：{exc}\n")


if __name__=="__main__":
    main()
