import csv
import os
import numpy as np
from scipy.stats import norm
import torch
from tqdm import trange
from validation.simulators.BlenderSimulator import BlenderSimulator
import pandas as pd
from sklearn.metrics import confusion_matrix
import seaborn as sns
import matplotlib.pyplot as plt

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

def replay(start_state, end_state, noise_mean, noise_std, agent_cfg, planner_cfg, camera_cfg, filter_cfg, get_rays_fn, render_fn, blender_cfg, density_fn):
    '''
    This function reads a CSV file and for each row where the last column is 'True', 
    it creates a BlenderSimulator instance and runs it with a noise vector derived from columns 3-14 of the row.

    Parameters:
        csv_file (str): The path to the CSV file.
        start_state (torch.Tensor): The starting state of the simulator.
        end_state (torch.Tensor): The ending state of the simulator.
        noise_mean (torch.Tensor): Means of disturbances.
        noise_std (torch.Tensor): Standard deviation of noises to use.
        agent_cfg (dict): The configuration for the agent.
        planner_cfg (dict): The configuration for the planner.
        camera_cfg (dict): The configuration for the camera.
        filter_cfg (dict): The configuration for the filter.
        get_rays_fn (function): A function to get rays.
        render_fn (function): A function to render the scene.
        blender_cfg (dict): The configuration for Blender.
        density_fn (function): A function to get the density of a point in space.
    '''

    file_list = os.listdir('results')
    csv_file_name = next((file for file in file_list if file.lower().endswith('.csv')), None)

    if csv_file_name:
        csv_file_path = os.path.join('results', csv_file_name)
        simulationData = {}

        with open(csv_file_path, 'r') as file:
            reader = csv.reader(file)
            for row in reader:
                if row[-1] == 'TRUE':
                    simulationNumber = row[0]
                    while True:
                        noise_vector = torch.from_numpy(np.array(row[2:14], dtype=np.float32)).to(device)
                        if simulationNumber not in simulationData:
                            simulationData[simulationNumber] = []
                        simulationData[simulationNumber].append(noise_vector)
                        if row[-2] == 'TRUE':
                            break
                        row = next(reader, None)  

    simulator = BlenderSimulator(start_state, end_state, agent_cfg, planner_cfg, camera_cfg, filter_cfg, get_rays_fn, render_fn, blender_cfg, density_fn)
    outputSimulationList = []

    print(f"Starting replay validation on BlenderSimulator")
    for simulationNumber, simulationSteps in simulationData.items():
        simulator.reset()
        simTrajLogLikelihood = 0
        print(f"Replaying simulation {simulationNumber} with {len(simulationSteps)} steps!")
        for step in trange(len(simulationSteps)):
            noise = simulationSteps[step]
            print(f"Replaying step {step} with noise: {noise}")
            isCollision, collisionVal, currentPos = simulator.step(noise)
            outputStepList = [simulationNumber, step]

            # append the noises
            noiseList = noise.cpu().numpy()

            outputStepList.extend(noiseList)
            
            # append the sdf value and positions
            outputStepList.append(collisionVal)
            outputStepList.extend(currentPos)

            # find and append the trajectory likelihood, both for this step and the entire trajectory
            curLogLikelihood = trajectoryLikelihood(noiseList, noise_mean, noise_std)
            outputStepList.append(curLogLikelihood)

            simTrajLogLikelihood += curLogLikelihood
            outputStepList.append(simTrajLogLikelihood)
            
            # output the collision value
            outputStepList.append(isCollision)
            
            # append the value of the step to the simulation data
            outputSimulationList.append(outputStepList)

            if isCollision:
                break

        os.makedirs('results/replays', exist_ok=True)
        with open("results/replays/collisionValuesReplay.csv", "w") as csvFile:
            writer = csv.writer(csvFile)
            for outputStepList in outputSimulationList:
                writer.writerow(outputStepList) 


def trajectoryLikelihood(noise, noise_mean_cpu, noise_std_cpu):
    # get the likelihood of a noise measurement by finding each element's probability, logging each, and returning the sum
    likelihoods = norm.pdf(noise, loc = noise_mean_cpu.cpu().numpy(), scale = noise_std_cpu.cpu().numpy())
    logLikelihoods = np.log(likelihoods)
    return logLikelihoods.sum()

def createConfusionMatrix(original_csv_path, new_results_csv_path):
    # load data
    original_df = pd.read_csv(original_csv_path)
    new_results_df = pd.read_csv(new_results_csv_path)

    # use last 
    y_actual = original_df.iloc[:, -1] == 'TRUE'  # Convert to boolean
    y_predicted = new_results_df.iloc[:, -1] == 'TRUE'  # Convert to boolean

    # create the confusion matrix
    conf_matrix = confusion_matrix(y_actual, y_predicted)

    # display confusion matrix using seaborn
    sns.heatmap(conf_matrix, annot=True, cmap='Blues', fmt='d')
    plt.xlabel('Predicted Labels')
    plt.ylabel('Actual Labels')
    plt.title('Confusion Matrix')
    plt.show()