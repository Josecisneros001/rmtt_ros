#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import time
import robomaster
from robomaster import robot
import rospy
import numpy as np
from std_msgs.msg import Int8, Float32, Empty, ColorRGBA
from geometry_msgs.msg import Vector3, Quaternion, Twist, Pose
from tf.broadcaster import TransformBroadcaster
from tf.transformations import quaternion_from_euler
import cv2
from sensor_msgs.msg import Range, Imu
from cv_bridge import CvBridge
from sensor_msgs.msg import Image


class RMTTDriver(object):
    IP_ADDRESS_STR = "192.168.10.2"
    ROBOT_ADDRESS_STR = "192.168.10.1"
    V_XY_MAX = 40
    V_Z_MAX = 60
    V_YAW_RATE_MAX = 50
    ACTIVE_FRONT_CAM = True
    FRONT_CAM_FREQ = 50.0

    def __init__(self):
        # Node Init
        rospy.init_node('rmtt_driver')
        
        # Cleanup when termniating the node
        rospy.on_shutdown(self.shutdown)
        
        # Load parameters
        RMTTDriver.IP_ADDRESS_STR = rospy.get_param("IP_ADDRESS_STR", RMTTDriver.IP_ADDRESS_STR)
        RMTTDriver.ROBOT_ADDRESS_STR = rospy.get_param("ROBOT_ADDRESS_STR", RMTTDriver.ROBOT_ADDRESS_STR)
        RMTTDriver.V_XY_MAX = rospy.get_param("V_XY_MAX", RMTTDriver.V_XY_MAX)
        RMTTDriver.V_Z_MAX = rospy.get_param("V_Z_MAX", RMTTDriver.V_Z_MAX)
        RMTTDriver.V_YAW_RATE_MAX = rospy.get_param("V_YAW_RATE_MAX", RMTTDriver.V_YAW_RATE_MAX)
        RMTTDriver.ACTIVE_FRONT_CAM = rospy.get_param("ACTIVE_FRONT_CAM", RMTTDriver.ACTIVE_FRONT_CAM)
        RMTTDriver.FRONT_CAM_FREQ = rospy.get_param("FRONT_CAM_FREQ", RMTTDriver.FRONT_CAM_FREQ)

        # Variables Init
        self.drone = robot.Drone()
        self.frequency = 100.0
        self.Ts = 1.0/self.frequency
        self.node_rate = rospy.Rate(self.frequency)
        self.drone_state = "LANDED"
        self.battery_state = "NA"
        self.bridge = CvBridge()
        self.shutdown_flag = False
        self.yaw = 0.0
        self.pitch = 0.0
        self.roll = 0.0

        # Publishers
        self.pubBtmRange = rospy.Publisher('btm_range', Float32, queue_size=10)
        self.pubFwdRange = rospy.Publisher('fwd_range', Float32, queue_size=10)
        self.pubImu = rospy.Publisher('imu', Imu, queue_size=5)
        self.pubImuAngle = rospy.Publisher('imu_angle', Float32, queue_size=5)
        self.pubBattery = rospy.Publisher('battery', Float32, queue_size=10)
        self.pubFrontCam = rospy.Publisher('front_cam/image_raw', Image, queue_size=10)


        # Subscribers
        rospy.Subscriber("takeoff", Empty, self.callBackTakeOff)
        rospy.Subscriber("land", Empty, self.callBackLand)
        rospy.Subscriber("shutdown", Empty, self.callBackShutdown)
        rospy.Subscriber("cmd_vel", Twist, self.callBackCmdVel)
        rospy.Subscriber("rgb_led", ColorRGBA, self.callBackRGBLed)

    def callBackShutdown(self):
        self.shutdown()
        self.shutdown_flag = True

    def shutdown(self):
        # Stop the robot
        try:
            rospy.loginfo("Stopping the drone...")
            self.drone.unsub_tof()
            self.drone.flight.unsub_attitude()
            self.drone.flight.unsub_imu()
            self.drone.battery.unsub_battery_info()  

            if (RMTTDriver.ACTIVE_FRONT_CAM):
                self.drone.camera.stop_video_stream()
            
            self.drone.close()
            rospy.sleep(2)
        except:
            pass
        rospy.loginfo("Shutting down RMTTDriver Node...")


    # ROS Callbacks

    def callBackTakeOff(self, data):
        if (self.drone_state=="LANDED"):
            self.drone.led.set_led_breath(freq=2, r=255, g=0, b=0)    
            self.drone.flight.takeoff().wait_for_completed()
            self.drone_state="FLYING"
            self.drone.led.set_led(r=0, g=255, b=0)    

    def callBackLand(self, data):
        if (self.drone_state=="FLYING"):
            self.drone.led.set_led_breath(freq=2, r=255, g=0, b=0)    
            self.drone.flight.land().wait_for_completed()
            self.drone_state="LANDED"
            self.drone.led.set_led(r=0, g=0, b=0)    
        
    def callBackCmdVel(self, data):
        # cmdvel linear(x,y,z)  angular(z)  all assumed to be in [-1,1]
        # roll, pitch, accelerate, yaw:  a,b,c,d [-100,100]
        vx = np.rint(100*np.clip(data.linear.x, -1.0, 1.0))
        vy = np.rint(100*np.clip(data.linear.y, -1.0, 1.0))
        vz = np.rint(100*np.clip(data.linear.z, -1.0, 1.0))
        v_yaw_rate = np.rint(100*np.clip(data.angular.z, -1.0, 1.0))
        
        # Saturate for safety.
        vx = np.clip(vx, -RMTTDriver.V_XY_MAX, RMTTDriver.V_XY_MAX)
        vy = np.clip(vy, -RMTTDriver.V_XY_MAX, RMTTDriver.V_XY_MAX)
        vz = np.clip(vz, -RMTTDriver.V_Z_MAX, RMTTDriver.V_Z_MAX)
        v_yaw_rate = np.clip(v_yaw_rate, -RMTTDriver.V_YAW_RATE_MAX, RMTTDriver.V_YAW_RATE_MAX)
        
        if (self.drone_state=="FLYING"):
            self.drone.flight.rc(a=-vy, b=vx, c=vz, d=-v_yaw_rate)

    def callBackRGBLed(self, data):
        self.drone.led.set_led(r=data.r, g=data.g, b=data.b)

    def readFrontCamera(self, timer):
        try:
            img = self.drone.camera.read_cv2_image()
            image_message = self.bridge.cv2_to_imgmsg(img, "rgb8")
            self.pubFrontCam.publish(image_message)
        except:
            pass

    def subTof(self, tof_cm):        
        tof_fwd_cm = self.drone.sensor.get_ext_tof()
        
        if (tof_cm>0):
            self.pubBtmRange.publish(Float32(tof_cm/100.0))
        else:
            self.pubBtmRange.publish(Float32(0.0))

        if (tof_fwd_cm==None):
            self.pubFwdRange.publish(Float32(np.nan))
        else:
            if (tof_fwd_cm>0):
                self.pubFwdRange.publish(Float32(tof_fwd_cm/100.0))
            else:
                self.pubFwdRange.publish(Float32(0.0))
    
    def subAttitude(self, attitude_angles):
        self.yaw, self.pitch, self.roll = attitude_angles

    
    def subImu(self, imu_info):
        vgx, vgy, vgz, agx, agy, agz = imu_info
        # agx = 0.01*agx
        # agy = 0.01*agy
        # agz = 0.01*agz
        imu_data = Imu()  
        imu_data.header.stamp = rospy.Time.now()
        imu_data.header.frame_id = "imu_link" 
        imu_data.orientation_covariance[0] = 1000000
        imu_data.orientation_covariance[1] = 0
        imu_data.orientation_covariance[2] = 0
        imu_data.orientation_covariance[3] = 0
        imu_data.orientation_covariance[4] = 1000000
        imu_data.orientation_covariance[5] = 0
        imu_data.orientation_covariance[6] = 0
        imu_data.orientation_covariance[7] = 0
        imu_data.orientation_covariance[8] = 0.000001

        newquat = quaternion_from_euler(self.roll, self.pitch, self.yaw)
        imu_data.orientation = Quaternion(newquat[0], newquat[1], newquat[2], newquat[3])
        imu_data.linear_acceleration_covariance[0] = -1
        imu_data.angular_velocity_covariance[0] = -1

        imu_data.linear_acceleration.x = agx
        imu_data.linear_acceleration.y = agy
        imu_data.linear_acceleration.z = agz

        imu_data.angular_velocity.x = vgx
        imu_data.angular_velocity.y = vgy
        imu_data.angular_velocity.z = vgz
        self.pubImu.publish(imu_data)
        self.pubImuAngle.publish(Float32(self.yaw))
        
    def subBatteryInfo(self, battery_info):
        battery_soc = battery_info
        self.pubBattery.publish(Float32(battery_soc))
        
        # warnings for different levels of battery state of charge
        if (self.battery_state=="NA"):  # Not Available
            if (battery_soc<30):
                self.battery_state = "MEDIUM"
                print("  battery: {0}".format(battery_soc))
        
        if (self.battery_state=="MEDIUM"):
            if (battery_soc<20):
                self.battery_state = "POOR"
                print("  [WARNING]  battery: {0}".format(battery_soc))
                
        if (self.battery_state=="POOR"):
            if (battery_soc<10):
                self.battery_state = "CRITICAL" 
                print("  [ALERT]  battery: {0}".format(battery_soc))

    def run(self):
        robomaster.config.LOCAL_IP_STR = RMTTDriver.IP_ADDRESS_STR
        robomaster.config.ROBOT_IP_STR = RMTTDriver.ROBOT_ADDRESS_STR
    
        print("\n**** RMTT ROS DRIVER ****")
        self.drone.initialize(conn_type="sta")
        print("  connexion to "+RMTTDriver.ROBOT_ADDRESS_STR+" ..... ok")
        drone_version = self.drone.get_sdk_version()
        print("  drone sdk version: {0}".format(drone_version))
        print("  Ready to fly!")
    
        self.drone.sub_tof(freq=10, callback=self.subTof)
        self.drone.flight.sub_attitude(10, self.subAttitude)
        self.drone.flight.sub_imu(10, self.subImu)
        self.drone.battery.sub_battery_info(freq=1, callback=self.subBatteryInfo)
        
    
        if (RMTTDriver.ACTIVE_FRONT_CAM):
            self.drone.camera.start_video_stream(display=False)
            self.drone.camera.set_fps("high")
            self.drone.camera.set_resolution("high")
            self.drone.camera.set_bitrate(6)  
            rospy.Timer(rospy.Duration(1.0 / RMTTDriver.FRONT_CAM_FREQ), self.readFrontCamera)
    
        rospy.spin()


if __name__ == '__main__':
    try:
        driver = RMTTDriver()
        driver.run()
    except rospy.ROSInterruptException:
        pass
