"""Retrain the stealth-check ML model:  python train_detector.py [clips_per_class]"""
import sys
from backend import detector

if __name__ == "__main__":
    detector.train(int(sys.argv[1]) if len(sys.argv) > 1 else 220)
